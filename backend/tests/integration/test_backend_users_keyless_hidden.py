"""Бэк реестра БЕЗ Admin API Key СКРЫТ, а `errors[]` означает только реальный сбой (ADR-080 §1).

**Инвертированный регресс-гейт.** Прежняя редакция файла (`test_backend_users_unqueried_sources.py`)
фиксировала обратное: бэк без ключа попадал в `errors[]` с сообщением «Admin API Key не задан
в CRM — бэк НЕ опрошен» (прод-инцидент `selquro`). ADR-080 §1 эту норму отменил: владелец
держит в реестре бэки, у которых Admin API нет и не планируется, поэтому постоянная жёлтая
плашка обесценила предупреждение — реальный сбой источника стал визуально неотличим от штатной
конфигурации. Исходная неотличимость «ничего не найдено» ↔ «бэк не опрашивался» закрывается
другим средством: фильтр приложений строится по `has_admin_api_key`, а пустое состояние прямо
говорит «подключите бэк с Admin API Key».

**Файл-гейт обязан остаться** (ADR-080 §1): без него возврат старой ветки прошёл бы молча.
Режим ОДНОГО бэка не меняется — явный `backend_id` без ключа по-прежнему `409
backend_admin_key_not_set` (осознанное действие оператора, а не фоновая конфигурация).

Список читается из снимка (ADR-080 §3), поэтому строки бэка с ключом здесь не проверяются —
их наполняет воркер (его покрытие — отдельные тесты). Здесь проверяется ровно состав
`errors[]` и код одиночного режима.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from app.api import deps
from app.infra.backend_admin_client import _clear_prefix_cache
from app.infra.crypto import encrypt_secret
from app.models.service_backend import Backend
from app.services.backend_user_service import BackendUserService
from backend_admin_helpers import FakeAdminTransport, install_transport
from conftest import make_principal
from httpx import ASGITransport, AsyncClient

KEYED_ID = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
NO_KEY_ID = uuid.UUID("00000000-0000-0000-0000-0000000000f2")
ADMIN_KEY = "admin-key-for-the-queried-backend"

USER_ROW: dict[str, Any] = {
    "id": "c18ae65b-a6ab-4a1b-a13a-40b4c0d23708",
    "external_id": "B8BB4E1D-0477-4D13-A292-524F62B460D2",
    "registered_at": "2026-08-13T17:01:14Z",
}


@pytest.fixture(autouse=True)
def _reset_prefix_cache() -> Iterator[None]:
    _clear_prefix_cache()
    yield
    _clear_prefix_cache()


class FakeBackendRepo:
    def __init__(self, backends: list[Backend]) -> None:
        self._items = backends

    async def list_all(self) -> list[Backend]:
        return list(self._items)

    async def get_by_id(self, backend_id: uuid.UUID) -> Backend | None:
        return next((b for b in self._items if b.id == backend_id), None)


class FakeSnapshotRepo:
    """Снимок-заглушка: пустая выдача и ни одной строки источника («ещё не собран»)."""

    async def source_states(self, backend_ids: list[uuid.UUID] | None = None) -> list[Any]:
        return []

    async def list_page(self, **_kwargs: Any) -> tuple[list[Any], int]:
        return [], 0


def _repo() -> FakeBackendRepo:
    return FakeBackendRepo(
        [
            Backend(
                id=KEYED_ID,
                code="veltrio",
                name="232",
                domain="https://veltriohub.shop/",
                admin_api_key_encrypted=encrypt_secret(ADMIN_KEY),
            ),
            Backend(
                id=NO_KEY_ID,
                code="selquro",
                name="Selquro",
                domain="https://selquro.shop/",
                admin_api_key_encrypted=None,
            ),
        ]
    )


def _app(repo: FakeBackendRepo) -> Any:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    app.dependency_overrides[deps.get_current_principal] = lambda: make_principal()
    app.dependency_overrides[deps.get_backend_user_service] = lambda: BackendUserService(
        repository=repo,  # type: ignore[arg-type]
        snapshots=FakeSnapshotRepo(),  # type: ignore[arg-type]
    )
    return app


def _transport(monkeypatch: pytest.MonkeyPatch) -> FakeAdminTransport:
    transport = FakeAdminTransport()
    # Детекция префикса контракта идёт probe-запросом `GET {P}/products` (ADR-072 §4а).
    transport.on("GET", "/products", status=200, json_body={"items": []})
    transport.on("GET", "/users", status=200, json_body={"total": 0, "items": []})
    transport.on(
        "GET",
        "/stats",
        status=200,
        json_body={"users_total": 0, "paid_users": 0, "payments_sum_usd": 0},
    )
    install_transport(monkeypatch, transport)
    return transport


async def test_backend_without_admin_key_is_absent_from_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Гейт: бэк без ключа НЕ порождает элемент `errors[]` (ADR-080 §1)."""
    _transport(monkeypatch)
    app = _app(_repo())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/backend-users", params={"search": USER_ROW["id"]})

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert all(e["backend_code"] != "selquro" for e in body["errors"])


async def test_registry_without_any_admin_key_returns_empty_without_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Реестр из одних бэков без ключа — пустой ответ БЕЗ жёлтой плашки.

    Пустое состояние страницы («подключите бэк с Admin API Key») объясняет ситуацию само;
    дублировать её элементом `errors[]` значило бы снова смешать конфигурацию со сбоем.
    """
    _transport(monkeypatch)
    repo = FakeBackendRepo(
        [
            Backend(
                id=NO_KEY_ID,
                code="selquro",
                name="Selquro",
                domain="https://selquro.shop/",
                admin_api_key_encrypted=None,
            )
        ]
    )

    async with AsyncClient(transport=ASGITransport(app=_app(repo)), base_url="http://test") as c:
        response = await c.get("/api/backend-users")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["errors"] == []
    # Снимка нет вовсе → метка свежести и расходы честно `null` (ADR-080 §6).
    assert body["snapshot_at"] is None
    assert body["api_costs"] is None


async def test_single_backend_without_key_is_still_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Режим ОДНОГО бэка не меняется: явный `backend_id` без ключа → 409 (ADR-080 §1)."""
    _transport(monkeypatch)
    app = _app(_repo())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/backend-users", params={"backend_id": str(NO_KEY_ID)})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "backend_admin_key_not_set"
