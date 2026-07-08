"""Контрактные тесты производного `UserListItem.status` через API (04-api.md, ADR-028).

Правило (нормативно, приоритет `is_active`): `is_active=false` → `"inactive"` (даже при
заданном `first_login_at`); `is_active=true` И `first_login_at IS NULL` → `"pending"`;
`is_active=true` И `first_login_at` задан → `"active"`. Внутренняя метка `first_login_at`
наружу не отдаётся — только производный `status`. Реальный `UserService` поверх in-memory
фейков (conftest.RbacFakeDb).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.api import deps
from app.services.role_service import RoleService
from app.services.team_service import TeamService
from app.services.user_service import UserService
from conftest import RbacFakeDb, make_principal
from httpx import ASGITransport, AsyncClient

_LOGGED_IN = datetime(2026, 6, 1, tzinfo=UTC)


def _build_app(db: RbacFakeDb) -> Any:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    app.dependency_overrides[deps.get_current_principal] = lambda: make_principal()
    app.dependency_overrides[deps.get_user_service] = lambda: UserService(
        users=db.user_repo, roles=db.role_repo, teams=db.team_repo
    )
    app.dependency_overrides[deps.get_role_service] = lambda: RoleService(repository=db.role_repo)
    app.dependency_overrides[deps.get_team_service] = lambda: TeamService(
        teams=db.team_repo, users=db.user_repo
    )
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_list_status_reflects_tristate_and_hides_first_login_at() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Ожидающий", role, is_active=True, first_login_at=None)
    db.add_user("Активный", role, is_active=True, first_login_at=_LOGGED_IN)
    db.add_user("Выключенный", role, is_active=False, first_login_at=_LOGGED_IN)
    app = _build_app(db)

    async with _client(app) as client:
        resp = await client.get("/api/users")

    assert resp.status_code == 200
    by_name = {u["username"]: u for u in resp.json()["items"]}
    assert by_name["Ожидающий"]["status"] == "pending"
    assert by_name["Активный"]["status"] == "active"
    # is_active=false приоритетен над заданным first_login_at → inactive.
    assert by_name["Выключенный"]["status"] == "inactive"
    # Внутренняя метка наружу не отдаётся.
    assert "first_login_at" not in resp.text


@pytest.mark.asyncio
async def test_created_user_status_is_pending_in_201_body() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    app = _build_app(db)

    async with _client(app) as client:
        created = await client.post(
            "/api/users",
            json={"username": "Новый", "password": "s3cret-pass", "role_id": str(role.id)},
        )

    assert created.status_code == 201
    body = created.json()
    # Новый пользователь ещё не входил (first_login_at=NULL) → «Ожидает входа».
    assert body["status"] == "pending"
    assert body["is_active"] is True
    assert "first_login_at" not in created.text


@pytest.mark.asyncio
async def test_patch_deactivate_sets_status_inactive_in_200_body() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Активный", role, is_active=True, first_login_at=_LOGGED_IN)
    app = _build_app(db)

    async with _client(app) as client:
        patched = await client.patch(f"/api/users/{user.id}", json={"is_active": False})

    assert patched.status_code == 200
    body = patched.json()
    assert body["is_active"] is False
    # Деактивация → inactive, несмотря на заданный first_login_at.
    assert body["status"] == "inactive"


@pytest.mark.asyncio
async def test_patch_reactivate_logged_in_user_status_active_in_200_body() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Возвращённый", role, is_active=False, first_login_at=_LOGGED_IN)
    app = _build_app(db)

    async with _client(app) as client:
        patched = await client.patch(f"/api/users/{user.id}", json={"is_active": True})

    assert patched.status_code == 200
    body = patched.json()
    assert body["is_active"] is True
    # Реактивация пользователя, который уже входил → active.
    assert body["status"] == "active"
