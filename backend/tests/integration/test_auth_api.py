from __future__ import annotations

import pytest
from app.api import deps
from app.config import get_settings
from app.infra.rate_limit import InMemoryRateLimiter
from app.services.auth_service import AuthService
from conftest import RbacFakeDb
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_login_me_and_auth_error_contracts() -> None:
    from app.main import create_app

    app = create_app(get_settings())
    db = RbacFakeDb()
    auth_service = AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=10, window_sec=300),
        user_repository=db.user_repo,
    )
    app.dependency_overrides[deps.get_auth_service] = lambda: auth_service
    # ADR-055 §5.1: `/me` под актором admin-уровня отдаёт ВСЕ команды системы ⇒ ходит в БД
    # (`TeamRepository.list_refs`). Postgres здесь не нужен — сессия фейковая, команд нет.
    app.dependency_overrides[deps.get_session] = lambda: db.session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        ok = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
            headers={"X-Real-IP": "203.0.113.10"},
        )
        bad_user = await client.post(
            "/api/auth/login",
            json={"username": "missing", "password": "secret"},
            headers={"X-Real-IP": "203.0.113.11"},
        )
        bad_password = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "bad"},
            headers={"X-Forwarded-For": "203.0.113.12, 10.0.0.1"},
        )
        no_token = await client.get("/api/auth/me")
        me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {ok.json()['access_token']}"},
        )

    assert ok.status_code == 200
    assert ok.json()["token_type"] == "bearer"
    assert me.status_code == 200
    # ADR-021: /me отдаёт профиль + права принципала (супер-админ → полный каталог).
    me_body = me.json()
    assert me_body["username"] == "admin"
    assert me_body["role"] == "admin"
    assert me_body["is_superadmin"] is True
    assert me_body["permissions"]["servers"] == ["view", "create", "edit", "delete"]
    assert "password" not in me.text
    assert no_token.status_code == 401
    assert no_token.json()["error"]["code"] == "unauthorized"
    assert bad_user.status_code == bad_password.status_code == 401
    assert bad_user.json() == bad_password.json()
    assert bad_user.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_rate_limit_uses_real_ip_headers() -> None:
    from app.main import create_app

    app = create_app(get_settings())
    auth_service = AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=2, window_sec=300),
        user_repository=RbacFakeDb().user_repo,
    )
    app.dependency_overrides[deps.get_auth_service] = lambda: auth_service

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for _ in range(2):
            await client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "bad"},
                headers={"X-Real-IP": "198.51.100.5"},
            )
        limited = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
            headers={"X-Real-IP": "198.51.100.5"},
        )
        other_ip = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
            headers={"X-Real-IP": "198.51.100.6"},
        )

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert other_ip.status_code == 200
