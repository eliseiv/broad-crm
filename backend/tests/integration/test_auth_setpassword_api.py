"""Контрактные тесты «открытого первого входа» через API (04-api.md#auth, ADR-025).

Реальные роутеры + `AuthService` поверх in-memory фейков (conftest.RbacFakeDb).
Проверяют: беспарольный вход → `password_setup_required:true` + setup-token (без
access); `POST /api/auth/set-password` с setup-token → access; access-token отвергается
set-password (401); setup-token отвергается ресурсным эндпоинтом (401 — limited-scope).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.api import deps
from app.config import get_settings
from app.db import get_session
from app.infra.rate_limit import InMemoryRateLimiter
from app.services.auth_service import AuthService
from conftest import RbacFakeDb
from httpx import ASGITransport, AsyncClient


def _build_app(db: RbacFakeDb) -> Any:
    from app.main import create_app

    app = create_app(get_settings())
    auth_service = AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=100, window_sec=300),
        user_repository=db.user_repo,
    )
    app.dependency_overrides[deps.get_auth_service] = lambda: auth_service

    # Ресурсные эндпоинты используют реальный get_current_principal; сессия БД не
    # запрашивается для отвергнутого setup-токена (decode падает раньше запроса), но
    # подменяем get_session на безопасную заглушку, чтобы не трогать реальную БД.
    async def _fake_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_session] = _fake_session
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_passwordless_login_returns_setup_token_via_api() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Беспарольный", role, password_hash=None)
    app = _build_app(db)

    async with _client(app) as client:
        resp = await client.post(
            "/api/auth/login",
            json={"username": "Беспарольный"},
            headers={"X-Real-IP": "203.0.113.20"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["password_setup_required"] is True
    assert body["setup_token"]
    # Access-token не выдаётся до установки пароля (exclude_none → ключа нет).
    assert "access_token" not in body


@pytest.mark.asyncio
async def test_set_password_with_setup_token_issues_access() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Беспарольный", role, password_hash=None)
    app = _build_app(db)

    async with _client(app) as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "Беспарольный"},
            headers={"X-Real-IP": "203.0.113.21"},
        )
        setup_token = login.json()["setup_token"]
        done = await client.post(
            "/api/auth/set-password",
            json={"password": "brand-new-pass"},
            headers={"Authorization": f"Bearer {setup_token}"},
        )

    assert done.status_code == 200
    body = done.json()
    assert body["password_setup_required"] is False
    assert body["access_token"]
    assert "setup_token" not in body


@pytest.mark.asyncio
async def test_set_password_weak_is_422_via_api() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Беспарольный", role, password_hash=None)
    app = _build_app(db)

    async with _client(app) as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "Беспарольный"},
            headers={"X-Real-IP": "203.0.113.22"},
        )
        setup_token = login.json()["setup_token"]
        weak = await client.post(
            "/api/auth/set-password",
            json={"password": "short"},
            headers={"Authorization": f"Bearer {setup_token}"},
        )

    assert weak.status_code == 422
    assert weak.json()["error"]["details"][0]["field"] == "password"


@pytest.mark.asyncio
async def test_set_password_already_set_is_409_via_api() -> None:
    from app.infra.passwords import hash_password

    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Беспарольный", role, password_hash=None)
    app = _build_app(db)

    async with _client(app) as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "Беспарольный"},
            headers={"X-Real-IP": "203.0.113.23"},
        )
        setup_token = login.json()["setup_token"]
        # Пароль устанавливается «гонкой» между выдачей setup-token и set-password.
        user.password_hash = hash_password("already-set-pass")
        conflict = await client.post(
            "/api/auth/set-password",
            json={"password": "brand-new-pass"},
            headers={"Authorization": f"Bearer {setup_token}"},
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "password_already_set"


@pytest.mark.asyncio
async def test_set_password_rejects_access_token_401() -> None:
    db = RbacFakeDb()
    app = _build_app(db)

    async with _client(app) as client:
        # Обычный access-token супер-админа.
        login = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
            headers={"X-Real-IP": "203.0.113.24"},
        )
        access = login.json()["access_token"]
        resp = await client.post(
            "/api/auth/set-password",
            json={"password": "brand-new-pass"},
            headers={"Authorization": f"Bearer {access}"},
        )

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_setup_token_rejected_by_resource_endpoint_401() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Беспарольный", role, password_hash=None)
    app = _build_app(db)

    async with _client(app) as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "Беспарольный"},
            headers={"X-Real-IP": "203.0.113.25"},
        )
        setup_token = login.json()["setup_token"]
        # Setup-token limited-scope: ресурсный эндпоинт (get_current_principal) отвергает.
        resp = await client.get("/api/users", headers={"Authorization": f"Bearer {setup_token}"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
