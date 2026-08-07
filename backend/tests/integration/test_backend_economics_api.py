"""API «Продукты и тарифы» (04-api.md#backend-economics, ADR-072) — RBAC, деградация, аудит.

Нормативные сценарии — 06-testing-strategy.md §«Backend Users + Backend Economics».
Модуль до этой волны покрыт НУЛЁМ тестов, поэтому приоритет — регресс-гейты, каждый из
которых стережёт конкретный уже осмысленный способ сломаться.

Устройство: РЕАЛЬНЫЕ роутер + сервис + клиент поверх in-memory реестра бэков; подменён
только upstream-ТРАНСПОРТ (`tests/backend_admin_helpers.py`). `dependency_overrides` на сам
`BackendAdminClient` не работает — он создаётся прямым вызовом внутри
`BackendAdminSourceResolver.client()` (`app/services/backend_admin_source.py:53`), поэтому
подменяется фабрика сервиса (`deps.get_backend_economics_service`) + транспорт клиента.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from app.api import deps
from app.infra import backend_admin_client as client_mod
from app.infra.backend_admin_client import (
    REASON_BAD_JSON,
    REASON_HTTP_4XX,
    REASON_HTTP_5XX,
    REASON_NOT_FOUND,
    REASON_REDIRECT,
    REASON_REJECTED,
    REASON_SCHEMA_MISMATCH,
    REASON_TIMEOUT,
    REASON_TRANSPORT,
    _clear_prefix_cache,
)
from app.infra.crypto import encrypt_secret
from app.models.service_backend import Backend
from app.services.backend_economics_service import BackendEconomicsService
from app.services.backend_user_service import BackendUserService
from backend_admin_helpers import FakeAdminTransport, RecordingLogger, Rule, install_transport
from conftest import make_principal
from httpx import ASGITransport, AsyncClient

BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
OTHER_BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e2")
NO_KEY_BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e3")
ADMIN_KEY = "super-secret-admin-key-value"

BASE = f"/api/backend-economics/{BACKEND_ID}"

# Пять путей роутера (04-api.md#backend-economics) — перечень для поэлементного RBAC.
ALL_PATHS: list[tuple[str, str]] = [
    ("GET", "/api/backend-economics/backends"),
    ("GET", f"{BASE}/products"),
    ("PATCH", f"{BASE}/products/p-1"),
    ("GET", f"{BASE}/pricing"),
    ("PATCH", f"{BASE}/pricing/t-1"),
]

WRITE_PATHS: list[tuple[str, str]] = [(m, p) for m, p in ALL_PATHS if m == "PATCH"]
READ_PATHS: list[tuple[str, str]] = [(m, p) for m, p in ALL_PATHS if m == "GET"]

PATCH_BODY: dict[str, dict[str, Any]] = {
    "products": {"tokens": 1500},
    "pricing": {"tokens": 2.5},
}


@pytest.fixture(autouse=True)
def _reset_prefix_cache() -> Iterator[None]:
    """Кэш префиксов клиента — process-global; без сброса тесты зависят от порядка."""
    _clear_prefix_cache()
    yield
    _clear_prefix_cache()


# --- Реестр бэков (in-memory) -------------------------------------------------


class FakeBackendRepo:
    """In-memory замена `BackendRepository` (подмножество, нужное резолверу источника)."""

    def __init__(self, backends: list[Backend]) -> None:
        self._items = backends

    async def list_all(self) -> list[Backend]:
        return list(self._items)

    async def get_by_id(self, backend_id: uuid.UUID) -> Backend | None:
        return next((b for b in self._items if b.id == backend_id), None)


def make_backend(backend_id: uuid.UUID, *, code: str, name: str, with_key: bool = True) -> Backend:
    return Backend(
        id=backend_id,
        code=code,
        name=name,
        domain="https://api.example.com/",
        admin_api_key_encrypted=encrypt_secret(ADMIN_KEY) if with_key else None,
    )


def make_repo() -> FakeBackendRepo:
    """Реестр: два бэка с ключом (сортировка по `name`) + один без ключа (в селектор не идёт)."""
    return FakeBackendRepo(
        [
            make_backend(OTHER_BACKEND_ID, code="beta", name="Beta API"),
            make_backend(BACKEND_ID, code="alpha", name="Alpha API"),
            make_backend(NO_KEY_BACKEND_ID, code="nokey", name="No Key API", with_key=False),
        ]
    )


def build_app(principal: Any, repo: FakeBackendRepo | None = None) -> Any:
    from app.config import get_settings
    from app.main import create_app

    repository = repo if repo is not None else make_repo()
    app = create_app(get_settings())
    app.dependency_overrides[deps.get_current_principal] = lambda: principal
    app.dependency_overrides[deps.get_backend_economics_service] = lambda: BackendEconomicsService(
        repository=repository  # type: ignore[arg-type]
    )
    app.dependency_overrides[deps.get_backend_user_service] = lambda: BackendUserService(
        repository=repository  # type: ignore[arg-type]
    )
    return app


def client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- Фикстуры данных бэка -----------------------------------------------------


def product_item(**overrides: Any) -> dict[str, Any]:
    item = {
        "product_id": "p-1",
        "name": "Базовый",
        "price": "990",
        "period": "month",
        "tokens": 1000,
        "avatar_tokens": 50,
        "grantable": True,
        "updated_at": "2026-08-01T10:00:00Z",
    }
    item.update(overrides)
    return item


def tariff_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "tariff_id": "t-1",
        "kind": "chat",
        "name": "Чат",
        "tokens": 1.5,
        "updated_at": None,
    }
    item.update(overrides)
    return item


# `limits` — RUNTIME-данные каждого бэка: заморожены ИМЕНА КЛЮЧЕЙ и ТИПЫ, но НЕ значения
# (ADR-072 §7.2). Числа ниже — данные ФИКСТУРЫ; они ассертятся только round-trip'ом
# (побуквенная трансляция), а не как нормативные границы.
LIMITS_FIXTURE: dict[str, Any] = {
    "product_tokens_max": 123_456,
    "product_avatar_tokens_max": 789,
    "tariff_tokens_max": 12.5,
    "tariff_decimal_places": 6,
}


def capabilities_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract_version": 11,
        "features": ["products.write_tokens", "pricing.write_tokens"],
        "limits": dict(LIMITS_FIXTURE),
        "cache_effective_after_seconds": 60,
    }
    body.update(overrides)
    return body


def product_update_body(**overrides: Any) -> dict[str, Any]:
    body = product_item(tokens=1500)
    body.update(
        {"previous_tokens": 1000, "changed": True, "effective_after_seconds": 60},
    )
    body.update(overrides)
    return body


def working_transport(monkeypatch: pytest.MonkeyPatch, **rules: Any) -> FakeAdminTransport:
    """Транспорт «всё исправно»: продукты, тарифы, capabilities, PATCH-и."""
    transport = FakeAdminTransport()
    transport.on(
        "GET", "/products", status=200, json_body=rules.get("products", {"items": [product_item()]})
    )
    transport.on(
        "GET", "/pricing", status=200, json_body=rules.get("pricing", {"items": [tariff_item()]})
    )
    transport.on(
        "GET", "/capabilities", status=200, json_body=rules.get("capabilities", capabilities_body())
    )
    transport.on("PATCH", "/products/p-1", status=200, json_body=product_update_body())
    transport.on(
        "PATCH",
        "/pricing/t-1",
        status=200,
        json_body=tariff_item(tokens=2.5)
        | {"previous_tokens": 1.5, "changed": True, "effective_after_seconds": 60},
    )
    install_transport(monkeypatch, transport)
    return transport


async def call(app: Any, method: str, path: str) -> httpx.Response:
    """Вызов пути роутера с телом, подходящим методу (PATCH — минимально валидное)."""
    async with client(app) as c:
        if method == "PATCH":
            key = "pricing" if "/pricing/" in path else "products"
            return await c.patch(path, json=PATCH_BODY[key])
        return await c.get(path)


# =============================================================================
# RBAC поэлементно (ADR-072 §2): ключ `backend-economics` — НЕ алиас `backend-users`
# =============================================================================


@pytest.mark.parametrize(("method", "path"), ALL_PATHS)
async def test_full_backend_users_rights_are_forbidden_on_every_economics_path(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    """Роль с полным `backend-users:["view","edit"]` и БЕЗ `backend-economics` → 403 на КАЖДОМ пути.

    Кейс на путь (а не один общий) — это и есть доказательство, что новый ключ не алиас:
    «где-то 403» не исключает, что другой путь пропускает по чужому праву.
    """
    working_transport(monkeypatch)
    app = build_app(
        make_principal(
            is_superadmin=False,
            role="Оператор",
            permissions={"backend-users": ["view", "edit"], "backends": ["view"]},
        )
    )

    response = await call(app, method, path)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(("method", "path"), READ_PATHS)
async def test_view_right_allows_reads(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    """`backend-economics:["view"]` без `edit` → GET 200 на каждом читающем пути."""
    working_transport(monkeypatch)
    app = build_app(
        make_principal(
            is_superadmin=False, role="Оператор", permissions={"backend-economics": ["view"]}
        )
    )

    response = await call(app, method, path)

    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path"), WRITE_PATHS)
async def test_view_right_forbids_writes(
    monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    """`backend-economics:["view"]` без `edit` → PATCH 403 на каждом пишущем пути."""
    working_transport(monkeypatch)
    app = build_app(
        make_principal(
            is_superadmin=False, role="Оператор", permissions={"backend-economics": ["view"]}
        )
    )

    response = await call(app, method, path)

    assert response.status_code == 403


async def test_backends_selector_does_not_require_backends_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /backends` доступен держателю `backend-economics:view` БЕЗ `backends:view`.

    Селектор страницы не должен зависеть от чужого права: режима «Все приложения» здесь
    нет, и без селектора страница нерабочая (04-api.md#backend-economics).
    """
    working_transport(monkeypatch)
    app = build_app(
        make_principal(
            is_superadmin=False, role="Экономист", permissions={"backend-economics": ["view"]}
        )
    )

    async with client(app) as c:
        response = await c.get("/api/backend-economics/backends")

    assert response.status_code == 200
    items = response.json()["items"]
    # Бэк без Admin API Key в селектор не попадает; сортировка — `name ASC`.
    assert [i["code"] for i in items] == ["alpha", "beta"]
    assert all(i["id"] != str(NO_KEY_BACKEND_ID) for i in items)


async def test_backend_user_not_found_is_unreachable_from_economics_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`backend_user_not_found` с этого роутера НЕДОСТИЖИМ (ADR-072 §4г).

    404 бэка на расширенном пути даёт `backend_admin_extension_not_supported`: путей с
    пользователем в роутере нет вовсе, и появление «пользователь не найден» = дефект.
    """
    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=404)
    app = build_app(make_principal())

    async with client(app) as c:
        # Прогреваем префикс v1-путём, чтобы 404 читался как «нет расширения», а не «нет контракта».
        await c.get(f"{BASE}/products")
        response = await c.patch(f"{BASE}/products/p-1", json={"tokens": 1500})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "backend_admin_extension_not_supported"
    assert response.json()["error"]["code"] != "backend_user_not_found"


# =============================================================================
# Деградация `/capabilities` — по кейсу на КАЖДЫЙ исход (ADR-072 §7.1)
# =============================================================================

# Девять строк закрытого перечня `reason`: значение сверяется ПОБУКВЕННО. Ассерт
# «`reason` есть» не засчитывается — исходным дефектом была именно ПОДМЕНА значения
# ближайшим по смыслу (DNS-сбой с пометкой `http_5xx` послал бы дежурного не туда).
CAPABILITIES_FAILURES: list[tuple[str, dict[str, Any]]] = [
    (REASON_NOT_FOUND, {"status": 404}),
    (REASON_TIMEOUT, {"exc": httpx.TimeoutException("read timeout")}),
    (REASON_TRANSPORT, {"exc": httpx.ConnectError("getaddrinfo failed")}),
    (REASON_REDIRECT, {"status": 302, "headers": {"Location": "https://other.example.com/"}}),
    (REASON_REJECTED, {"status": 401}),
    (REASON_HTTP_4XX, {"status": 400}),
    (REASON_HTTP_5XX, {"status": 500}),
    (REASON_BAD_JSON, {"status": 200, "text_body": "<html>not json</html>"}),
    (REASON_SCHEMA_MISMATCH, {"status": 200, "json_body": ["not", "an", "object"]}),
]


@pytest.mark.parametrize(
    ("reason", "failure"), CAPABILITIES_FAILURES, ids=[r for r, _ in CAPABILITIES_FAILURES]
)
@pytest.mark.parametrize("resource", ["products", "pricing"])
async def test_capabilities_failure_degrades_to_null_without_failing_the_list(
    monkeypatch: pytest.MonkeyPatch, reason: str, failure: dict[str, Any], resource: str
) -> None:
    """ЛЮБОЙ неуспех необязательного подзапроса ⇒ 200 + список целиком + `capabilities: null`.

    Плюс именованное событие `backend_admin_capabilities_unavailable` с `backend_id` и
    ТОЧНЫМ `reason`, и обязательный негативный ассерт «код ответа не 502»: провал всего
    списка из-за необязательного подзапроса — ровно тот дефект, против которого норма.
    """
    import app.services.backend_economics_service as service_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(service_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("GET", "/capabilities", **failure)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/{resource}")

    assert response.status_code == 200
    assert response.status_code != 502
    body = response.json()
    assert body["capabilities"] is None
    # Список отдан ЦЕЛИКОМ — деградация фич не режет данные.
    assert len(body["items"]) == 1
    assert body["items"][0]["product_id" if resource == "products" else "tariff_id"] in (
        "p-1",
        "t-1",
    )

    events = recorder.named("backend_admin_capabilities_unavailable")
    assert len(events) == 1
    assert events[0]["reason"] == reason
    assert events[0]["backend_id"] == str(BACKEND_ID)
    # Значение admin-ключа в событие не попадает ни при какой причине.
    assert not recorder.contains_value(ADMIN_KEY)


async def test_capabilities_valid_json_off_schema_is_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON разобран как объект, но НЕ по схеме → `schema_mismatch` (ветка сервиса).

    Отличается от `bad_json` (тело не разбирается вовсе) и от `schema_mismatch` клиента
    (тело — не объект): здесь объект есть, но обязательных полей нет.
    """
    import app.services.backend_economics_service as service_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(service_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("GET", "/capabilities", status=200, json_body={"unexpected": "shape"})
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    assert response.json()["capabilities"] is None
    assert recorder.named("backend_admin_capabilities_unavailable")[0]["reason"] == (
        REASON_SCHEMA_MISMATCH
    )


async def test_write_feature_comes_from_features_not_from_presence_of_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`features` БЕЗ `products.write_tokens` отражён в ответе, хотя поле `tokens` отдано.

    Признак записи выводится из `features`, а не из наличия поля: бэк вправе отдавать
    токены read-only.
    """
    working_transport(
        monkeypatch,
        capabilities=capabilities_body(features=["pricing.write_tokens"]),
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    body = response.json()
    assert body["items"][0]["tokens"] == 1000  # поле есть
    assert body["capabilities"]["features"] == ["pricing.write_tokens"]
    assert "products.write_tokens" not in body["capabilities"]["features"]


# =============================================================================
# Обязательность полей `capabilities` — ПАРА кейсов, доказывающая критерий с ОБЕИХ
# сторон (ADR-072 §7.2). Обязательность поля БЕЗ потребителя в CRM превращает
# безобидное умолчание конформного бэка в `schema_mismatch` ⇒ `capabilities: null` ⇒
# молча read-only страницу при любом праве. Обязательность `features` — наоборот,
# ЕДИНСТВЕННОЕ, что удерживает fail-closed.
# =============================================================================


@pytest.mark.parametrize(
    "optional_field", ["contract_version", "cache_effective_after_seconds", "limits"]
)
async def test_capabilities_without_optional_field_is_valid_not_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch, optional_field: str
) -> None:
    """Ответ `/capabilities` БЕЗ необязательного поля валиден: `capabilities` НЕ `null`.

    Кейс на КАЖДУЮ строку критерия (ADR-072 §7.2). `contract_version` и
    `cache_effective_after_seconds` CRM не показывает вовсе (задержку оператор видит из
    `effective_after_seconds` ответа `PATCH`), `limits` имеет штатное отсутствие
    («проверки границ нет, форма работоспособна»). `schema_mismatch` здесь = дефект:
    он даёт `capabilities: null` и молча гасит правку при любом праве.
    """
    import app.services.backend_economics_service as service_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(service_mod, "logger", recorder)

    body = capabilities_body()
    del body[optional_field]
    working_transport(monkeypatch, capabilities=body)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities is not None, f"отсутствие `{optional_field}` не должно гасить фичи"
    # Признак записи доехал — на клиенте карандаши остаются.
    assert "products.write_tokens" in capabilities["features"]
    assert capabilities[optional_field] is None
    # Событие деградации НЕ пишется: это не неуспех подзапроса.
    assert recorder.named("backend_admin_capabilities_unavailable") == []


async def test_capabilities_with_features_only_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ, состоящий ТОЛЬКО из `features`, валиден — все прочие поля необязательны."""
    working_transport(
        monkeypatch, capabilities={"features": ["products.write_tokens", "pricing.write_tokens"]}
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities is not None
    assert capabilities["features"] == ["products.write_tokens", "pricing.write_tokens"]
    assert capabilities["limits"] is None
    assert capabilities["contract_version"] is None
    assert capabilities["cache_effective_after_seconds"] is None


async def test_patch_without_limits_reaches_backend_without_crm_side_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без `limits` правка НЕ блокируется: значение доходит до бэка (границ в схеме CRM нет).

    Вторая половина кейса «`limits` нет — работаем»: верхних границ в схемах запроса CRM
    намеренно нет (ADR-072 §7.2), поэтому проверка ложится на бэк — и именно поэтому его
    `422` обязан маппиться, а не превращаться в `502` (§7.3).
    """
    transport = working_transport(monkeypatch, capabilities={"features": ["products.write_tokens"]})
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json={"tokens": 10_000_000})

    assert response.status_code == 200
    # Значение ушло наверх без обрезки/отказа на стороне CRM.
    patches = [r for r in transport.requests if r.method == "PATCH"]
    assert len(patches) == 1
    assert json.loads(patches[0].content) == {"tokens": 10_000_000}


async def test_capabilities_without_features_is_null_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ ОБРАТНЫЙ кейс: ответ БЕЗ `features` ⇒ `capabilities: null` ⇒ read-only.

    Без этого кейса ничто не помешает в будущем сделать опциональным и `features`,
    потеряв fail-closed: страница осталась бы с карандашами при бэке, который запись
    не подтверждал. Пара с кейсами выше и ЕСТЬ проверка критерия обязательности.
    """
    import app.services.backend_economics_service as service_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(service_mod, "logger", recorder)

    working_transport(
        monkeypatch,
        capabilities={"contract_version": 11, "limits": dict(LIMITS_FIXTURE)},
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200  # список всё равно отдан целиком
    assert response.json()["capabilities"] is None
    assert len(response.json()["items"]) == 1
    assert recorder.named("backend_admin_capabilities_unavailable")[0]["reason"] == (
        REASON_SCHEMA_MISMATCH
    )


# =============================================================================
# Транзит отказа бэка: `400` И `422` ⇒ ОДИН код `backend_admin_bad_request`
# (ADR-072 §7.3, 04-api.md#коды-ошибок-модуля)
# =============================================================================

# Нормативный фолбэк берётся ИЗ ПРОД-КОНСТАНТЫ, а не переписывается строкой: расхождение
# текста в тесте и в коде иначе осталось бы незамеченным.
FALLBACK = client_mod._VALIDATION_REJECTED_FALLBACK

DETAIL_FORMATS: list[tuple[str, Any, str]] = [
    # (а) `detail`-строка (формат `400` контрагента) — идёт транзитом.
    ("string", {"detail": "tokens: значение больше допустимого"}, "tokens: значение больше"),
    # (б) `detail`-СПИСОК (формат `422` FastAPI) — `msg` первого элемента + поле из `loc`.
    (
        "fastapi_list",
        {
            "detail": [
                {
                    "loc": ["body", "tokens"],
                    "msg": "Input should be less than or equal to 500000",
                    "type": "less_than_equal",
                }
            ]
        },
        "tokens: Input should be less than or equal to 500000",
    ),
    # (в) формат неизвлекаем — нормативный фолбэк (называет причину и действие).
    ("no_detail_key", {"error": "boom"}, FALLBACK),
    ("empty_detail_list", {"detail": []}, FALLBACK),
    ("list_of_non_objects", {"detail": ["boom"]}, FALLBACK),
    ("item_without_msg", {"detail": [{"loc": ["body", "tokens"], "type": "x"}]}, FALLBACK),
]


@pytest.mark.parametrize("upstream_status", [400, 422])
@pytest.mark.parametrize(
    ("case", "upstream_body", "expected"),
    DETAIL_FORMATS,
    ids=[c for c, _, _ in DETAIL_FORMATS],
)
async def test_backend_rejection_maps_to_bad_request_with_readable_reason(
    monkeypatch: pytest.MonkeyPatch,
    upstream_status: int,
    case: str,
    upstream_body: dict[str, Any],
    expected: str,
) -> None:
    """`400` и `422` бэка ⇒ `400 backend_admin_bad_request` с человекочитаемой причиной.

    Межрепозиторный стык: у контрагента отказ валидации тела (границы/точность) — это
    **`422`**, а не `400`, и путь ШТАТНЫЙ (без `limits` значение доходит до бэка).
    Обязательные негативные ассерты: ответ CRM **не** `502` и в сообщении **нет**
    строки «Ошибка бэка (HTTP 422)» — так выглядела бы необработанная ветка «прочий
    не-2xx», из-за которой оператор видел бы номер статуса вместо причины.
    """
    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=upstream_status, json_body=upstream_body)
    app = build_app(make_principal())

    async with client(app) as c:
        await c.get(f"{BASE}/products")  # прогрев префикса
        response = await c.patch(f"{BASE}/products/p-1", json={"tokens": 10_000_000})

    assert response.status_code == 400
    assert response.status_code != 502
    error = response.json()["error"]
    assert error["code"] == "backend_admin_bad_request"
    assert expected in error["message"]
    assert f"HTTP {upstream_status}" not in error["message"]
    assert "Ошибка бэка" not in error["message"]


@pytest.mark.parametrize("upstream_status", [400, 422])
async def test_backend_rejection_with_unparsable_body_falls_back(
    monkeypatch: pytest.MonkeyPatch, upstream_status: int
) -> None:
    """Тело отказа вообще не JSON → тот же код и нормативный фолбэк, а не 502."""
    transport = working_transport(monkeypatch)
    transport.on(
        "PATCH", "/products/p-1", status=upstream_status, text_body="<html>Bad Request</html>"
    )
    app = build_app(make_principal())

    async with client(app) as c:
        await c.get(f"{BASE}/products")
        response = await c.patch(f"{BASE}/products/p-1", json={"tokens": 5})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "backend_admin_bad_request"
    assert response.json()["error"]["message"] == FALLBACK


async def test_pricing_patch_422_maps_to_bad_request_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же маппинг на втором пишущем пути — тариф (точность `tariff_decimal_places`)."""
    transport = working_transport(monkeypatch)
    transport.on(
        "PATCH",
        "/pricing/t-1",
        status=422,
        json_body={
            "detail": [
                {"loc": ["body", "tokens"], "msg": "Слишком много знаков", "type": "decimal"}
            ]
        },
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/pricing/t-1", json={"tokens": 0.1234567})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "backend_admin_bad_request"
    assert response.json()["error"]["message"] == "tokens: Слишком много знаков"
    assert "HTTP 422" not in response.json()["error"]["message"]


@pytest.mark.parametrize("upstream_status", [400, 422])
async def test_rejected_patch_writes_no_audit(
    monkeypatch: pytest.MonkeyPatch, upstream_status: int
) -> None:
    """Отказ бэка `400`/`422` ⇒ аудит НЕ пишется (изменения не было)."""
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=upstream_status, json_body={"detail": "нет"})
    app = build_app(make_principal())

    async with client(app) as c:
        await c.get(f"{BASE}/products")
        response = await c.patch(f"{BASE}/products/p-1", json={"tokens": 1500})

    assert response.status_code == 400
    assert recorder.named("backend_admin_action") == []


# --- `limits`: заморожены имена ключей и типы, НЕ значения (ADR-072 §7.2) ------


async def test_limits_full_set_is_transmitted_key_for_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Полный набор `limits` транслируется ПОБУКВЕННО (переименование/схлопывание = дефект).

    Сверка идёт round-trip'ом с фикстурой: конкретные ЧИСЛА границ — runtime-данные бэка,
    ассертить их как норму запрещено (ADR-072 §7.2).
    """
    working_transport(monkeypatch)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    limits = response.json()["capabilities"]["limits"]
    assert set(limits) == set(LIMITS_FIXTURE)
    assert limits == LIMITS_FIXTURE


async def test_limits_with_part_of_keys_missing_is_valid_and_never_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отсутствие ЧАСТИ ключей `limits` валидно: недостающие → `null`, ответ 200, не 502."""
    working_transport(
        monkeypatch,
        capabilities=capabilities_body(limits={"product_tokens_max": 10}),
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    limits = response.json()["capabilities"]["limits"]
    assert limits["product_tokens_max"] == 10
    # Незаданные ключи нормализуются в `null`, а не выпадают из ответа.
    assert limits["product_avatar_tokens_max"] is None
    assert limits["tariff_tokens_max"] is None
    assert limits["tariff_decimal_places"] is None


async def test_capabilities_without_limits_at_all_is_valid_and_never_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отсутствие `limits` ЦЕЛИКОМ валидно: `capabilities` есть, `limits: null`, не 502."""
    working_transport(monkeypatch, capabilities=capabilities_body(limits=None))
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    assert response.json()["capabilities"] is not None
    assert response.json()["capabilities"]["limits"] is None


async def test_limits_unknown_key_is_ignored_not_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """Незнакомый ключ `limits` игнорируется (forward-compatibility), а не даёт 502."""
    working_transport(
        monkeypatch,
        capabilities=capabilities_body(
            limits=dict(LIMITS_FIXTURE) | {"brand_new_limit_from_future": 1}
        ),
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    limits = response.json()["capabilities"]["limits"]
    assert "brand_new_limit_from_future" not in limits
    assert limits == LIMITS_FIXTURE


# =============================================================================
# Совместимость v1: асимметрия products ↔ pricing (ADR-072 §1.1 п.5)
# =============================================================================


async def test_product_without_v11_fields_is_normalized_to_null_not_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ответ бэка БЕЗ новых полей продукта валиден: отсутствующее поле → `null`, не 502.

    Ассертится именно `null` в ответе CRM, а не отсутствие ключа: клиент отличает «не
    отдано» от «не выдаётся» (`grantable`) и рисует `—`, а не «Нет».
    """
    working_transport(
        monkeypatch,
        products={"items": [{"product_id": "p-1", "name": "Базовый"}]},
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    item = response.json()["items"][0]
    for field in ("tokens", "avatar_tokens", "grantable", "updated_at", "price", "period"):
        assert field in item, f"поле {field} обязано присутствовать в ответе CRM"
        assert item[field] is None, f"поле {field} обязано нормализоваться в null"


@pytest.mark.parametrize("missing", ["tokens", "tariff_id", "kind"])
async def test_pricing_item_missing_required_field_is_502(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Элемент `/pricing` без обязательного поля → `502 backend_admin_unavailable`.

    Кейс стережёт, чтобы «опциональность» продуктов (путь v1) не была скопирована на
    тарифы (путь существует только в v1.1) — там отсутствие поля есть contract mismatch.
    """
    item = tariff_item()
    del item[missing]
    working_transport(monkeypatch, pricing={"items": [item]})
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/pricing")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "backend_admin_unavailable"


async def test_products_list_over_limit_is_contract_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ответ `/products` с > 500 элементами → `502` (списки не пагинируются, ADR-072 §1.7)."""
    working_transport(
        monkeypatch,
        products={"items": [product_item(product_id=f"p-{i}") for i in range(501)]},
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "backend_admin_unavailable"


# --- `scope`: страницы читают каталог по-разному ------------------------------


async def test_economics_products_send_scope_all_and_backend_users_send_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Продукты и тарифы» шлют `scope=all`; «Юзеры бэков» не шлют `scope` вовсе.

    Умолчание бэка (`grantable`) сохраняет поведение формы «Установить план» — поэтому
    ассертится строка query ФАКТИЧЕСКОГО upstream-запроса, а не намерение кода.
    """
    transport = working_transport(monkeypatch)
    app = build_app(make_principal())

    async with client(app) as c:
        economics = await c.get(f"{BASE}/products")
    assert economics.status_code == 200
    assert transport.queries_for("GET", "/products") == ["scope=all"]

    transport.requests.clear()
    async with client(app) as c:
        users = await c.get(f"/api/backend-users/{BACKEND_ID}/products")
    assert users.status_code == 200
    assert transport.queries_for("GET", "/products") == [""]


# =============================================================================
# Аудит правки (ADR-072 §10): деталь называет ИМЕННО изменённое поле
# =============================================================================


def audit_events(recorder: RecordingLogger) -> list[dict[str, Any]]:
    return recorder.named("backend_admin_action")


async def patch_product(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], upstream: dict[str, Any]
) -> tuple[httpx.Response, RecordingLogger]:
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=200, json_body=upstream)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json=body)
    return response, recorder


async def test_audit_detail_names_tokens_only_when_tokens_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тело только с `tokens` → в `detail` есть `tokens=<prev>-><new>` и НЕТ `avatar_tokens=`."""
    response, recorder = await patch_product(
        monkeypatch,
        {"tokens": 1500},
        product_update_body(),
    )

    assert response.status_code == 200
    events = audit_events(recorder)
    assert len(events) == 1
    assert events[0]["action"] == "product_tokens_updated"
    assert events[0]["backend_id"] == str(BACKEND_ID)
    detail = events[0]["detail"]
    assert "tokens=1000->1500" in detail
    assert "avatar_tokens=" not in detail


async def test_audit_detail_names_avatar_tokens_without_fake_tokens_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тело только с `avatar_tokens` → есть `avatar_tokens=<new>` и НЕТ дельты `tokens=`.

    Обязательный НЕГАТИВНЫЙ ассерт (ADR-072 §10): `previous_tokens` относится только к
    `tokens`, поэтому безусловная дельта дала бы `tokens=1000->1000` — запись сообщала бы
    о правке, которой не было, и умалчивала о той, которая была.
    """
    response, recorder = await patch_product(
        monkeypatch,
        {"avatar_tokens": 60},
        product_update_body(tokens=1000, avatar_tokens=60),
    )

    assert response.status_code == 200
    detail = audit_events(recorder)[0]["detail"]
    assert "avatar_tokens=60" in detail
    # `tokens=` вне состава `avatar_tokens=` отсутствует, и ни одной дельты нет вовсе.
    assert re.search(r"(?<!avatar_)tokens=", detail) is None
    assert "->" not in detail
    assert "1000->1000" not in detail


async def test_audit_detail_names_both_values_when_both_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тело с обеими величинами → в `detail` ОБЕ части одной записью."""
    response, recorder = await patch_product(
        monkeypatch,
        {"tokens": 1500, "avatar_tokens": 60},
        product_update_body(avatar_tokens=60),
    )

    assert response.status_code == 200
    detail = audit_events(recorder)[0]["detail"]
    assert "tokens=1000->1500" in detail
    assert "avatar_tokens=60" in detail
    assert "product_id=p-1" in detail


async def test_audit_records_pricing_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный PATCH тарифа → действие `pricing_updated` с дельтой."""
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)
    working_transport(monkeypatch)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/pricing/t-1", json={"tokens": 2.5})

    assert response.status_code == 200
    events = audit_events(recorder)
    assert len(events) == 1
    assert events[0]["action"] == "pricing_updated"
    assert "tariff_id=t-1" in events[0]["detail"]
    assert "tokens=1.5->2.5" in events[0]["detail"]


@pytest.mark.parametrize("upstream_status", [400, 409, 500, 404])
async def test_audit_is_not_written_when_backend_rejects(
    monkeypatch: pytest.MonkeyPatch, upstream_status: int
) -> None:
    """Отказ бэка (любой не-2xx) ⇒ аудит НЕ пишется.

    Лог не должен утверждать об изменении, которого не было: правка глобальна и без отката.
    """
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=upstream_status, json_body={"detail": "нет"})
    app = build_app(make_principal())

    async with client(app) as c:
        await c.get(f"{BASE}/products")  # прогрев префикса
        response = await c.patch(f"{BASE}/products/p-1", json={"tokens": 1500})

    assert response.status_code != 200
    assert audit_events(recorder) == []


async def test_audit_never_contains_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """В аудит-событии НЕТ значения admin-ключа (он уходит только заголовком)."""
    _, recorder = await patch_product(monkeypatch, {"tokens": 1500}, product_update_body())

    assert audit_events(recorder), "успешная правка обязана порождать событие"
    assert not recorder.contains_value(ADMIN_KEY)


# =============================================================================
# Источник бэка: реестр и admin-ключ
# =============================================================================


# =============================================================================
# Архив продуктов (contract v1.2, ADR-073)
# =============================================================================


def patch_bodies(transport: FakeAdminTransport) -> list[Any]:
    """Разобранные тела фактических upstream-`PATCH`-запросов (в порядке отправки)."""
    return [json.loads(r.content) for r in transport.requests if r.method == "PATCH"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("archived", False), ("archived", True), ("tokens", 0), ("avatar_tokens", 0)],
    ids=["archived_false", "archived_true", "tokens_zero", "avatar_tokens_zero"],
)
async def test_falsy_value_reaches_backend_as_present_key(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    """FALSY-значение обязано ДОЙТИ до бэка ключом со значением, а не исчезнуть из тела.

    Отбор значимых полей идёт по `is not None`, а не по истинности (ADR-073 §1). Тихая
    поломка сделала бы **возврат из архива невозможным**: `archived: false` выпал бы из
    тела, бэк не увидел бы команды, а CRM отрапортовала бы успех. `tokens: 0` /
    `avatar_tokens: 0` — тот же класс: обнуление начисления это законная правка.
    Ассертится тело ФАКТИЧЕСКОГО upstream-запроса, а не намерение кода.
    """
    transport = working_transport(monkeypatch)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json={field: value})

    assert response.status_code == 200
    body = patch_bodies(transport)[0]
    assert field in body, f"поле {field} обязано присутствовать в теле upstream-запроса"
    assert body[field] == value
    assert body[field] is not None


async def test_patch_with_only_archived_is_valid_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """`archived` — ЗНАЧИМОЕ поле: тело с одним им валидно (не `400 validation_error`)."""
    working_transport(monkeypatch)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json={"archived": True})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("case", "body"),
    [
        ("empty", {}),
        ("only_if_updated_at", {"if_updated_at": "2026-08-01T10:00:00Z"}),
    ],
    ids=["empty", "only_if_updated_at"],
)
async def test_patch_without_meaningful_field_is_400(
    monkeypatch: pytest.MonkeyPatch, case: str, body: dict[str, Any]
) -> None:
    """Пустое тело и тело с одним `if_updated_at` → `400`: значимого поля нет.

    `if_updated_at` — защита от «двух операторов», а не изменяемая величина (ADR-073 §1).
    """
    transport = working_transport(monkeypatch)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json=body)

    assert response.status_code == 400
    # Запрос к бэку не уходит вовсе — отказ собственный, а не транзитный.
    assert patch_bodies(transport) == []


async def test_products_transit_includes_archived_without_server_side_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CRM отдаёт И активные, И архивные с флагами: фильтрация КЛИЕНТСКАЯ (ADR-073 §3).

    Негативные ассерты: в upstream-запрос не добавилось НИ ОДНОГО нового параметра —
    `scope=all` как был, и никакого `archived`/`include_archived`. Серверная фильтрация
    сделала бы переключатель «Показать архивные» неработающим.
    """
    transport = working_transport(
        monkeypatch,
        products={
            "items": [
                product_item(product_id="p-active", archived=False),
                product_item(product_id="p-archived", archived=True),
            ]
        },
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"{BASE}/products")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["product_id"] for i in items] == ["p-active", "p-archived"]
    assert [i["archived"] for i in items] == [False, True]
    # Параметры запроса к бэку не менялись волной ADR-073.
    assert transport.queries_for("GET", "/products") == ["scope=all"]


async def test_grant_plan_products_still_send_no_scope_and_carry_archived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Форма «Установить план» получает признак архива, а `scope` по-прежнему НЕ шлётся.

    Без трансляции поля фронту нечем пометить опцию (ADR-073 §5); появление `scope` на
    этом пути изменило бы состав каталога формы.
    """
    transport = working_transport(
        monkeypatch,
        products={
            "items": [
                product_item(product_id="p-active", archived=False),
                product_item(product_id="p-archived", archived=True),
            ]
        },
    )
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.get(f"/api/backend-users/{BACKEND_ID}/products")

    assert response.status_code == 200
    items = response.json()["items"]
    assert {i["product_id"]: i["archived"] for i in items} == {
        "p-active": False,
        "p-archived": True,
    }
    assert transport.queries_for("GET", "/products") == [""]


@pytest.mark.parametrize("path", ["backend-economics", "backend-users"])
async def test_product_without_archived_field_is_null_not_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Ответ бэка БЕЗ поля `archived` валиден: `archived: null`, статус 200, не 502.

    `null` = «у бэка нет самого понятия архива» ⇒ все продукты активны. Это штатное
    состояние совместимости, а не `schema_mismatch` (ADR-073 §1).
    """
    item = product_item()
    assert "archived" not in item  # фикстура v1.1 — поля нет вовсе
    working_transport(monkeypatch, products={"items": [item]})
    app = build_app(make_principal())

    url = (
        f"{BASE}/products"
        if path == "backend-economics"
        else f"/api/backend-users/{BACKEND_ID}/products"
    )
    async with client(app) as c:
        response = await c.get(url)

    assert response.status_code == 200
    assert response.status_code != 502
    assert response.json()["items"][0]["archived"] is None


# --- Аудит: имя действия называет ИЗМЕНЁННОЕ (ADR-073 §7) ---------------------


async def patch_and_capture(
    monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], upstream: dict[str, Any]
) -> tuple[httpx.Response, RecordingLogger]:
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=200, json_body=upstream)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json=body)
    return response, recorder


@pytest.mark.parametrize("archived", [True, False], ids=["archive", "unarchive"])
async def test_archived_only_patch_writes_archive_event_and_no_tokens_event(
    monkeypatch: pytest.MonkeyPatch, archived: bool
) -> None:
    """Только `archived` ⇒ РОВНО одно `product_archived_updated` и НИ ОДНОГО токенного.

    Обязательный негативный ассерт (ADR-073 §7): запись «правил токены» без правки
    токенов — та же ложь, что дельта `tokens=1000->1000` при неизменённом значении.
    Деталь пишется в форме ПРОВОДА (`true`/`false` строчными), а не питоновской.
    """
    response, recorder = await patch_and_capture(
        monkeypatch,
        {"archived": archived},
        product_update_body(archived=archived),
    )

    assert response.status_code == 200
    archive_events = recorder.named("backend_admin_action")
    assert len(archive_events) == 1
    assert archive_events[0]["action"] == "product_archived_updated"
    assert archive_events[0]["action"] != "product_tokens_updated"
    detail = archive_events[0]["detail"]
    assert detail == f"product_id=p-1 archived={'true' if archived else 'false'}"
    assert "True" not in detail and "False" not in detail
    assert "tokens=" not in detail


async def test_tokens_only_patch_writes_no_archive_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """Только `tokens` ⇒ РОВНО одно `product_tokens_updated` и ни одного архивного."""
    response, recorder = await patch_and_capture(
        monkeypatch, {"tokens": 1500}, product_update_body()
    )

    assert response.status_code == 200
    events = recorder.named("backend_admin_action")
    assert len(events) == 1
    assert events[0]["action"] == "product_tokens_updated"
    assert [e["action"] for e in events] != ["product_archived_updated"]
    assert "archived=" not in events[0]["detail"]


async def test_tokens_and_archived_patch_writes_two_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тело с `tokens` И `archived` ⇒ ДВА события: каждое называет своё изменение."""
    response, recorder = await patch_and_capture(
        monkeypatch,
        {"tokens": 1500, "archived": True},
        product_update_body(archived=True),
    )

    assert response.status_code == 200
    events = recorder.named("backend_admin_action")
    assert [e["action"] for e in events] == ["product_tokens_updated", "product_archived_updated"]
    assert "tokens=1000->1500" in events[0]["detail"]
    assert events[1]["detail"] == "product_id=p-1 archived=true"


# --- Толерантность ответа `PATCH` на archived-only правке (ADR-073 §8) --------


@pytest.mark.parametrize(
    "missing",
    [
        ["previous_tokens"],
        ["changed"],
        ["effective_after_seconds"],
        ["previous_tokens", "changed", "effective_after_seconds"],
    ],
    ids=["no_previous_tokens", "no_changed", "no_effective_after", "none_of_three"],
)
async def test_archived_patch_tolerates_incomplete_response_and_still_audits(
    monkeypatch: pytest.MonkeyPatch, missing: list[str]
) -> None:
    """Ответ `200` без `previous_tokens`/`changed`/`effective_after_seconds` ⇒ CRM `200` + аудит.

    ADR-073 §8(2): схема ответа CRM ТОЛЕРАНТНА — она парсит ответ ПОСЛЕ уже состоявшегося
    необратимого side-effect (прецедент ADR-057 §5). §8(3): аудит пишется НЕЗАВИСИМО от
    полноты ответа. Второй ассерт обязателен: строгая схема поднимает исключение ДО записи
    аудита, и дефект выглядит как «архив у бэка переключён, CRM показала красное, следа нет».
    """
    upstream = product_update_body(archived=True)
    for field in missing:
        del upstream[field]

    response, recorder = await patch_and_capture(monkeypatch, {"archived": True}, upstream)

    assert response.status_code == 200
    assert response.status_code != 502
    events = recorder.named("backend_admin_action")
    assert len(events) == 1, "аудит обязан фиксировать СОСТОЯВШИЙСЯ факт у бэка"
    assert events[0]["action"] == "product_archived_updated"
    assert events[0]["detail"] == "product_id=p-1 archived=true"


@pytest.mark.parametrize(
    "missing",
    [
        ["previous_tokens"],
        ["changed"],
        ["effective_after_seconds"],
        ["previous_tokens", "changed", "effective_after_seconds"],
    ],
    ids=["no_previous_tokens", "no_changed", "no_effective_after", "none_of_three"],
)
async def test_pricing_patch_tolerates_incomplete_response_and_still_audits(
    monkeypatch: pytest.MonkeyPatch, missing: list[str]
) -> None:
    """Толерантность §8 действует и на ВТОРОМ пишущем пути — `PATCH …/pricing/{tariff_id}`.

    Кейс обязателен ОТДЕЛЬНО от продуктовых (ADR-073 §8 п.4): реализовать толерантность
    только у продукта — ровно то расхождение схем двух `PATCH`'ей одного контракта, из-за
    которого норма и уточнялась. Аудит `pricing_updated` обязан быть записан: правка у
    бэка уже состоялась, и её след не зависит от полноты ответа.
    """
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    upstream = tariff_item(tokens=2.5) | {
        "previous_tokens": 1.5,
        "changed": True,
        "effective_after_seconds": 60,
    }
    for field in missing:
        del upstream[field]

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/pricing/t-1", status=200, json_body=upstream)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/pricing/t-1", json={"tokens": 2.5})

    assert response.status_code == 200
    assert response.status_code != 502
    events = recorder.named("backend_admin_action")
    assert len(events) == 1
    assert events[0]["action"] == "pricing_updated"


# --- `2xx` с НЕГОДНЫМ телом: ошибка не вправе опередить запись аудита (§8 п.3) ---
#
# Последний путь потери следа: до фикса разбор тела бросал исключение В КЛИЕНТЕ — раньше,
# чем аудит успевал записаться. Оператор получал `502` при УЖЕ переключённом у бэка
# признаке и НОЛЬ событий в следе. Опциональность трёх полей (§8 п.2) этот путь не
# закрывает: она про схему, а негодное тело падает в ветках РАЗБОРА до неё.

# Тела, которые бэк отдаёт со статусом `2xx`, но использовать их нельзя.
UNUSABLE_BODIES: list[tuple[str, dict[str, Any]]] = [
    ("json_array", {"json_body": []}),
    ("not_json_html", {"text_body": "<html><body>200 OK</body></html>"}),
    ("json_scalar", {"json_body": "ok"}),
]


@pytest.mark.parametrize(("case", "body"), UNUSABLE_BODIES, ids=[c for c, _ in UNUSABLE_BODIES])
async def test_product_2xx_with_unusable_body_is_502_but_audit_is_written(
    monkeypatch: pytest.MonkeyPatch, case: str, body: dict[str, Any]
) -> None:
    """`200` с негодным телом ⇒ CRM `502`, НО след правки записан (ADR-073 §8.3).

    Ассертится именно СОВМЕСТНОСТЬ «502 + событие есть»: до фикса здесь было НОЛЬ
    событий — это и есть регресс-гейт. `2xx` означает, что операция у бэка СОСТОЯЛАСЬ,
    поэтому аудит обязан зафиксировать ФАКТ, а не КАЧЕСТВО ответа.
    """
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=200, **body)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json={"archived": True})

    assert response.status_code == 502
    # Контракт на проводе НЕ изменился — отличимый тип ошибки виден только Python-коду.
    assert response.json()["error"]["code"] == "backend_admin_unavailable"

    events = recorder.named("backend_admin_action")
    assert events != [], "след состоявшейся правки не должен теряться из-за негодного тела"
    assert len(events) == 1
    assert events[0]["action"] == "product_archived_updated"
    # Ответ не разобран ⇒ величина берётся из ОТПРАВЛЕННОГО оператором значения
    # (`PATCH` идемпотентен — он устанавливает именно его).
    assert events[0]["detail"] == "product_id=p-1 archived=true"


@pytest.mark.parametrize(("case", "body"), UNUSABLE_BODIES, ids=[c for c, _ in UNUSABLE_BODIES])
async def test_product_2xx_unusable_body_writes_both_events_when_body_had_both(
    monkeypatch: pytest.MonkeyPatch, case: str, body: dict[str, Any]
) -> None:
    """Тело с `tokens` И `archived` ⇒ при негодном ответе записаны ОБА события.

    Полнота следа не зависит от разбираемости ответа: сколько величин оператор изменил,
    столько записей и обязано быть.
    """
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/products/p-1", status=200, **body)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/products/p-1", json={"tokens": 1500, "archived": False})

    assert response.status_code == 502
    events = recorder.named("backend_admin_action")
    assert [e["action"] for e in events] == ["product_tokens_updated", "product_archived_updated"]
    # Прежнее значение из ответа недоступно ⇒ дельта неполна, но НОВОЕ названо.
    assert events[0]["detail"] == "product_id=p-1 tokens=n/a->1500"
    assert events[1]["detail"] == "product_id=p-1 archived=false"


@pytest.mark.parametrize(("case", "body"), UNUSABLE_BODIES, ids=[c for c, _ in UNUSABLE_BODIES])
async def test_pricing_2xx_with_unusable_body_is_502_but_audit_is_written(
    monkeypatch: pytest.MonkeyPatch, case: str, body: dict[str, Any]
) -> None:
    """Симметричный кейс на втором пишущем пути — тариф (ADR-073 §8.3).

    Реализовать защиту только у продукта — ровно то расхождение двух `PATCH`'ей одного
    контракта, из-за которого норма и уточнялась.
    """
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    transport.on("PATCH", "/pricing/t-1", status=200, **body)
    app = build_app(make_principal())

    async with client(app) as c:
        response = await c.patch(f"{BASE}/pricing/t-1", json={"tokens": 2.5})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "backend_admin_unavailable"
    events = recorder.named("backend_admin_action")
    assert len(events) == 1
    assert events[0]["action"] == "pricing_updated"
    assert events[0]["detail"] == "tariff_id=t-1 tokens=n/a->2.5"


# --- ГРАНИЦА: без неё гейт выше «чинится» слишком широко ----------------------
#
# Аудит обязан молчать всюду, где бэк НЕ подтвердил применение. Писать след там, где
# факт не состоялся, ХУЖЕ исходного дефекта: лог начинает утверждать об изменениях,
# которых не было, и перестаёт быть доказательством вообще.

NOT_APPLIED_OUTCOMES: list[tuple[str, dict[str, Any]]] = [
    ("conflict_409", {"status": 409}),
    ("unprocessable_422", {"status": 422, "json_body": {"detail": "вне границ"}}),
    ("server_error_500", {"status": 500}),
    ("timeout", {"exc": httpx.TimeoutException("read timeout")}),
    ("transport", {"exc": httpx.ConnectError("getaddrinfo failed")}),
]


@pytest.mark.parametrize(
    ("case", "rule"), NOT_APPLIED_OUTCOMES, ids=[c for c, _ in NOT_APPLIED_OUTCOMES]
)
@pytest.mark.parametrize("resource", ["products", "pricing"])
async def test_no_audit_when_backend_did_not_confirm_application(
    monkeypatch: pytest.MonkeyPatch, case: str, rule: dict[str, Any], resource: str
) -> None:
    """Бэк НЕ подтвердил применение (`409`/`422`/`5xx`/таймаут/транспорт) ⇒ аудит ПУСТ.

    Обязательная пара к гейту «`2xx` с негодным телом»: там аудит пишется, потому что
    статус `2xx` — подтверждение факта; здесь подтверждения нет, и запись была бы ложью.
    """
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)

    transport = working_transport(monkeypatch)
    if resource == "products":
        transport.on("PATCH", "/products/p-1", **rule)
        url, payload = f"{BASE}/products/p-1", {"archived": True}
    else:
        transport.on("PATCH", "/pricing/t-1", **rule)
        url, payload = f"{BASE}/pricing/t-1", {"tokens": 2.5}
    app = build_app(make_principal())

    async with client(app) as c:
        await c.get(f"{BASE}/products")  # прогрев префикса
        response = await c.patch(url, json=payload)

    assert response.status_code != 200
    assert recorder.named("backend_admin_action") == []


async def test_unknown_backend_is_404_and_backend_without_key_is_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бэка нет в реестре → `404 backend_not_found`; ключа нет → `409 backend_admin_key_not_set`."""
    working_transport(monkeypatch)
    app = build_app(make_principal())

    async with client(app) as c:
        missing = await c.get(f"/api/backend-economics/{uuid.uuid4()}/products")
        no_key = await c.get(f"/api/backend-economics/{NO_KEY_BACKEND_ID}/products")

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "backend_not_found"
    assert no_key.status_code == 409
    assert no_key.json()["error"]["code"] == "backend_admin_key_not_set"


async def test_v1_backend_blocks_pricing_but_keeps_backend_users_page_working(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бэк уровня v1: `/pricing` → 502 `backend_admin_extension_not_supported`,

    при этом v1-функции страницы «Юзеры бэков» продолжают работать (регресс-гейт: вызов
    расширения не ломает уже работавшее).
    """
    transport = FakeAdminTransport(default=Rule(status=404))
    transport.on("GET", "/products", status=200, json_body={"items": [product_item()]})
    install_transport(monkeypatch, transport)
    app = build_app(make_principal())

    async with client(app) as c:
        pricing = await c.get(f"{BASE}/pricing")
        products = await c.get(f"/api/backend-users/{BACKEND_ID}/products")

    assert pricing.status_code == 502
    assert pricing.json()["error"]["code"] == "backend_admin_extension_not_supported"
    assert products.status_code == 200
    assert products.json()["items"][0]["product_id"] == "p-1"
