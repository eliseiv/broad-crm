from __future__ import annotations

import pytest
from app.api import deps
from app.config import get_settings
from app.infra.rate_limit import InMemoryRateLimiter
from app.services.auth_service import AuthService
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_login_me_and_auth_error_contracts() -> None:
    from app.main import create_app

    app = create_app(get_settings())
    auth_service = AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=10, window_sec=300),
    )
    app.dependency_overrides[deps.get_auth_service] = lambda: auth_service

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
    assert me.json() == {"username": "admin"}
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
