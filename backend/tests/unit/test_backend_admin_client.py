"""Клиент CRM Admin API бэков — семантика 404, детекция префикса, экранирование, заголовки.

Нормативные сценарии — 06-testing-strategy.md §«Backend Users + Backend Economics»
(блок «Клиент бэков»), решения — ADR-072 §1/§4. Модуль покрывает клиент напрямую
(`app/infra/backend_admin_client.py`); ответ CRM целиком и деградация `/capabilities`
покрыты интеграционно (`tests/integration/test_backend_economics_api.py`).

Подмена — на уровне ТРАНСПОРТА (`tests/backend_admin_helpers.py`): клиент создаётся прямым
вызовом внутри сервиса, `dependency_overrides` его не перехватывает.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from app.errors import AppError
from app.infra import backend_admin_client as client_mod
from app.infra.backend_admin_client import (
    ADMIN_ACTOR_HEADER,
    ADMIN_KEY_HEADER,
    PREFIX_CANDIDATES,
    BackendAdminClient,
    _clear_prefix_cache,
)
from backend_admin_helpers import FakeAdminTransport, RecordingLogger, Rule, install_transport

BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
DOMAIN = "https://api.example.com/"
ADMIN_KEY = "super-secret-admin-key-value"

# Порядок кандидатов детекции берётся из прод-константы, а не дублируется строками.
PRIMARY_PREFIX, ALT_PREFIX = PREFIX_CANDIDATES


@pytest.fixture(autouse=True)
def _reset_prefix_cache() -> Iterator[None]:
    """Кэш префиксов — process-global (`_prefix_cache`), иначе тесты зависят от порядка."""
    _clear_prefix_cache()
    yield
    _clear_prefix_cache()


def make_client() -> BackendAdminClient:
    return BackendAdminClient(backend_id=BACKEND_ID, domain=DOMAIN, admin_key=ADMIN_KEY)


def products_body() -> dict[str, Any]:
    return {"items": [{"product_id": "p-1", "name": "Базовый"}]}


# --- Семантика 404: по кейсу на КАЖДОЕ значение (ADR-072 §4б) ------------------


async def test_extended_path_404_on_known_prefix_is_extension_not_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`PATCH /products/{id}` + 404 при ИЗВЕСТНОМ префиксе → расширение не поддержано.

    Гейт против копипаста метода с зашитой «пользовательской» семантикой: на пути, где
    пользователя нет вовсе, `backend_user_not_found` был бы ложью (ADR-072 §4б).
    """
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("PATCH", "/products/p-1", status=404)
    install_transport(monkeypatch, transport)

    client = make_client()
    await client.list_products()  # прогревает префикс

    with pytest.raises(AppError) as exc:
        await client.update_product("p-1", body={"tokens": 5}, actor="crm:x")

    assert exc.value.code == "backend_admin_extension_not_supported"
    assert exc.value.code != "backend_user_not_found"
    assert exc.value.status_code == 502


async def test_user_path_404_on_known_prefix_is_backend_user_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`GET /users/{id}` + 404 → `backend_user_not_found` (прежнее поведение не сломано)."""
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("GET", "/users/u-404", status=404)
    install_transport(monkeypatch, transport)

    client = make_client()
    await client.list_products()  # прогревает префикс

    with pytest.raises(AppError) as exc:
        await client.get_user("u-404")

    assert exc.value.code == "backend_user_not_found"
    assert exc.value.status_code == 404


async def test_cold_cache_user_path_404_is_user_not_found_not_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ХОЛОДНЫЙ кэш + `/users/{id}` несуществующего пользователя (ADR-072 §4а.1).

    Латентный дефект v1: до разведения probe и запроса этот путь возвращал
    `backend_admin_not_supported` («бэк не реализует контракт») вместо «нет пользователя».
    """
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("GET", "/users/u-404", status=404)
    install_transport(monkeypatch, transport)

    with pytest.raises(AppError) as exc:
        await make_client().get_user("u-404")

    assert exc.value.code == "backend_user_not_found"
    assert exc.value.code != "backend_admin_not_supported"


async def test_cold_cache_extended_path_detects_prefix_by_v1_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ХОЛОДНЫЙ кэш + расширенный путь на бэке с префиксом `/v1/admin`.

    Регресс-гейт (ADR-072 §4а): без разведения probe и расширения первый же вызов
    `/pricing` дал бы 404 на ОБОИХ кандидатах, CRM объявила бы контракт нереализованным
    (`backend_admin_not_supported`) и префикс не закэшировался бы вовсе.
    """
    transport = FakeAdminTransport()
    transport.on("GET", f"{ALT_PREFIX}/products", status=200, json_body=products_body())
    transport.on("GET", f"{ALT_PREFIX}/pricing", status=200, json_body={"items": []})
    install_transport(monkeypatch, transport)

    client = make_client()
    result = await client.list_pricing()

    assert result == {"items": []}
    # Probe идёт по v1-пути и перебирает кандидатов; расширение уходит по определённому.
    assert transport.paths == [
        ("GET", f"{PRIMARY_PREFIX}/products"),
        ("GET", f"{ALT_PREFIX}/products"),
        ("GET", f"{ALT_PREFIX}/pricing"),
    ]

    # Префикс закэширован: повторный расширенный вызов идёт без probe.
    await client.list_pricing()
    assert transport.paths[-1] == ("GET", f"{ALT_PREFIX}/pricing")
    assert len(transport.requests) == 4


async def test_cold_cache_costs_exactly_two_requests_warm_costs_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Цена probe: холодный кэш — РОВНО два upstream-запроса, тёплый — один."""
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("GET", "/pricing", status=200, json_body={"items": []})
    install_transport(monkeypatch, transport)

    client = make_client()
    await client.list_pricing()

    assert transport.paths == [
        ("GET", f"{PRIMARY_PREFIX}/products"),
        ("GET", f"{PRIMARY_PREFIX}/pricing"),
    ]

    transport.requests.clear()
    await client.list_pricing()
    assert transport.paths == [("GET", f"{PRIMARY_PREFIX}/pricing")]


async def test_products_unavailable_blocks_contract_even_if_users_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/products` недоступен, `/users` работает → `502 backend_admin_not_supported`.

    Осознанно: `/products` обязателен в v1 и является путём детекции префикса.
    """
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=404)
    transport.on("GET", "/users", status=200, json_body={"items": [], "total": 0})
    install_transport(monkeypatch, transport)

    with pytest.raises(AppError) as exc:
        await make_client().list_users(limit=10, offset=0)

    assert exc.value.code == "backend_admin_not_supported"
    assert exc.value.status_code == 502


# --- Прочие коды бэка ---------------------------------------------------------


async def test_conflict_from_backend_maps_to_backend_admin_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """409 бэка → `backend_admin_conflict` (а не безликое «Ошибка бэка (HTTP 409)»)."""
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("PATCH", "/products/p-1", status=409)
    install_transport(monkeypatch, transport)

    with pytest.raises(AppError) as exc:
        await make_client().update_product("p-1", body={"tokens": 5}, actor="crm:x")

    assert exc.value.code == "backend_admin_conflict"
    assert exc.value.status_code == 409


async def test_bad_request_from_backend_keeps_backend_detail_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 на PATCH с неизвестным `product_id` → `backend_admin_bad_request` с текстом бэка."""
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on(
        "PATCH",
        "/products/unknown",
        status=400,
        json_body={"detail": "Неизвестный product_id"},
    )
    install_transport(monkeypatch, transport)

    with pytest.raises(AppError) as exc:
        await make_client().update_product("unknown", body={"tokens": 5}, actor="crm:x")

    assert exc.value.code == "backend_admin_bad_request"
    assert exc.value.status_code == 400
    assert exc.value.message == "Неизвестный product_id"


async def test_unprocessable_from_backend_shares_bad_request_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`422` бэка разбирается ТОЙ ЖЕ веткой, что `400` (ADR-072 §7.3), а не «прочий не-2xx».

    Регресс-гейт межрепозиторного стыка: у контрагента отказ валидации тела — `422`;
    попав в общую ветку, он дал бы `502 backend_admin_unavailable` с голым
    «Ошибка бэка (HTTP 422)» вместо причины отказа.
    """
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on(
        "PATCH",
        "/products/p-1",
        status=422,
        json_body={"detail": [{"loc": ["body", "tokens"], "msg": "too big", "type": "le"}]},
    )
    install_transport(monkeypatch, transport)

    with pytest.raises(AppError) as exc:
        await make_client().update_product("p-1", body={"tokens": 10**9}, actor="crm:x")

    assert exc.value.code == "backend_admin_bad_request"
    assert exc.value.status_code == 400
    assert exc.value.code != "backend_admin_unavailable"
    assert exc.value.message == "tokens: too big"
    assert "HTTP 422" not in exc.value.message


# `loc` FastAPI смешивает имена полей и ЧИСЛОВЫЕ индексы элементов; имя поля — последняя
# СТРОКА хвоста, индексы пропускаются (иначе в тексте оператору уехал бы номер).
DETAIL_LOC_CASES: list[tuple[str, Any, str]] = [
    ("plain_field", ["body", "tokens"], "tokens: слишком много"),
    ("indexed_field", ["body", 0, "tokens"], "tokens: слишком много"),
    ("trailing_index", ["body", "items", 3], "items: слишком много"),
    ("only_indexes", [0, 1], "слишком много"),
    ("loc_absent", None, "слишком много"),
]


@pytest.mark.parametrize(
    ("case", "loc", "expected"), DETAIL_LOC_CASES, ids=[c for c, _, _ in DETAIL_LOC_CASES]
)
async def test_validation_detail_names_field_from_loc_tail(
    monkeypatch: pytest.MonkeyPatch, case: str, loc: Any, expected: str
) -> None:
    """Имя поля берётся из хвоста `loc`; числовые индексы в текст не попадают."""
    item: dict[str, Any] = {"msg": "слишком много", "type": "value_error"}
    if loc is not None:
        item["loc"] = loc

    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("PATCH", "/products/p-1", status=422, json_body={"detail": [item]})
    install_transport(monkeypatch, transport)

    with pytest.raises(AppError) as exc:
        await make_client().update_product("p-1", body={"tokens": 5}, actor="crm:x")

    assert exc.value.message == expected


async def test_validation_detail_uses_first_item_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Из списка берётся ПЕРВЫЙ элемент — сообщение не склеивается из всех ошибок."""
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on(
        "PATCH",
        "/products/p-1",
        status=422,
        json_body={
            "detail": [
                {"loc": ["body", "tokens"], "msg": "первая", "type": "a"},
                {"loc": ["body", "avatar_tokens"], "msg": "вторая", "type": "b"},
            ]
        },
    )
    install_transport(monkeypatch, transport)

    with pytest.raises(AppError) as exc:
        await make_client().update_product("p-1", body={"tokens": 5}, actor="crm:x")

    assert exc.value.message == "tokens: первая"
    assert "вторая" not in exc.value.message


# --- Экранирование идентификатора в ПУТИ (регресс-гейт) -----------------------


async def test_product_id_traversal_does_not_change_upstream_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`product_id="../users/x"` НЕ меняет путь upstream-запроса (`_segment`, `safe=""`).

    Ассертится СЫРОЙ путь (`raw_path`): `url.path` httpx декодирует обратно, и на нём
    гейт был бы фиктивным. Без экранирования держатель `backend-economics:edit` направил
    бы PATCH с admin-ключом CRM на произвольный admin-путь бэка.
    """
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.default = Rule(status=200, json_body={})
    install_transport(monkeypatch, transport)

    client = make_client()
    await client.list_products()
    transport.requests.clear()

    await client.update_product("../users/x", body={"tokens": 5}, actor="crm:x")

    raw_path = transport.requests[0].url.raw_path.decode("ascii")
    assert raw_path == f"{PRIMARY_PREFIX}/products/..%2Fusers%2Fx"
    assert "/users/x" not in raw_path


# --- Заголовки: ключ не в логах, актор — на PATCH ------------------------------


async def test_admin_key_never_reaches_logs_on_success_or_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`X-Admin-Key` уходит ТОЛЬКО заголовком и не попадает в лог ни на одной ветке."""
    recorder = RecordingLogger()
    monkeypatch.setattr(client_mod, "logger", recorder)

    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("GET", "/pricing", status=500)
    transport.on("GET", "/users/u-1", exc=httpx.ConnectError("getaddrinfo failed"))
    install_transport(monkeypatch, transport)

    client = make_client()
    await client.list_products()  # успех
    with pytest.raises(AppError):
        await client.list_pricing()  # 5xx
    with pytest.raises(AppError):
        await client.get_user("u-1")  # транспортный сбой

    assert transport.requests[0].headers[ADMIN_KEY_HEADER] == ADMIN_KEY
    assert recorder.events, "клиент обязан логировать хотя бы детекцию префикса"
    assert not recorder.contains_value(ADMIN_KEY)


async def test_patch_sends_admin_actor_header_and_get_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """На PATCH уходит `X-Admin-Actor` формата `crm:<uuid>`; на GET заголовка нет."""
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body=products_body())
    transport.on("PATCH", "/products/p-1", status=200, json_body={})
    install_transport(monkeypatch, transport)

    actor_id = uuid.uuid4()
    client = make_client()
    await client.list_products()
    await client.update_product("p-1", body={"tokens": 5}, actor=f"crm:{actor_id}")

    assert ADMIN_ACTOR_HEADER not in transport.requests[0].headers
    assert transport.requests[1].headers[ADMIN_ACTOR_HEADER] == f"crm:{actor_id}"
