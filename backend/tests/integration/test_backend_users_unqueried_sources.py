"""Бэк реестра БЕЗ admin-ключа виден в `errors[]`, а не исчезает молча (ADR-069).

**Прод-инцидент, который закрывает этот файл.** Инстанс `selquro` был в реестре CRM и под
мониторингом (`/health` раз в минуту), но Admin API Key ему не задали. `list_with_admin_key()`
отфильтровывал его без следа, поэтому поиск существующего пользователя по `user_id` И по
`apphud_id` давал «Ничего не найдено» — неотличимо от «такого пользователя нет». Сам бэк при
этом находил пользователя обоими способами за 0.15 с: дефект был в НЕМОТЕ агрегации, а не в
поиске. Тест фиксирует, что такой бэк называется оператору по имени.
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
        repository=repo  # type: ignore[arg-type]
    )
    return app


def _transport(monkeypatch: pytest.MonkeyPatch) -> FakeAdminTransport:
    transport = FakeAdminTransport()
    # Детекция префикса контракта идёт probe-запросом `GET {P}/products` (ADR-072 §4а):
    # без этого правила бэк с ключом объявляется «не поддерживает CRM Admin API».
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


async def test_backend_without_admin_key_is_named_in_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Поиск не нашёл пользователя, и CRM ГОВОРИТ, что один бэк не опрашивался."""
    _transport(monkeypatch)
    app = _app(_repo())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/backend-users", params={"search": USER_ROW["id"]})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert [(e["backend_code"], e["message"]) for e in body["errors"]] == [
        ("selquro", "Admin API Key не задан в CRM — бэк НЕ опрошен")
    ]


async def test_registry_without_any_admin_key_still_explains_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ни одного опрашиваемого бэка — пустой ответ обязан НЕСТИ причину, а не просто ноль.

    Без этого экран «Нет данных: подключите бэк с Admin API Key» появлялся и тогда, когда
    бэки подключены, а ключа нет ровно у того, где живёт искомый пользователь.
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
    assert [e["backend_code"] for e in body["errors"]] == ["selquro"]


async def test_keyed_backend_is_still_queried_and_returns_its_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Регресс-гейт: предупреждение не подменяет данные — бэк с ключом опрашивается как раньше."""
    transport = _transport(monkeypatch)
    transport.on("GET", "/users", status=200, json_body={"total": 1, "items": [USER_ROW]})
    app = _app(_repo())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/backend-users", params={"search": USER_ROW["id"]})

    body = response.json()
    assert [item["id"] for item in body["items"]] == [USER_ROW["id"]]
    assert [item["backend_code"] for item in body["items"]] == ["veltrio"]
    assert len(body["errors"]) == 1
