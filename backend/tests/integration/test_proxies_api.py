"""Контрактные/интеграционные тесты роутера прокси (04-api.md#proxies).

Сервис замокан через dependency_overrides (как в test_ai_keys_api). Проверяются коды
и схемы ответов: POST 202 с `ProxyListItem` (has_password, БЕЗ пароля в любом виде),
невалидный proxy_type → 422, port вне диапазона → 422, отсутствующее поле → 400,
отсутствие JWT → 401, GET список, GET status (404 proxy_not_found), DELETE 204 и
повтор → 404. Пароль (plaintext) не присутствует ни в одном ответе.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api import deps
from app.errors import proxy_not_found
from app.models.proxy import ProxyStatus, ProxyType
from app.schemas.proxy import ProxyListItem, ProxyListResponse, ProxyStatusResponse
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

SECRET_PASSWORD = "s3cr3t-PROXY-PASSWORD-xyz"


class FakeProxyService:
    existing_id = uuid.UUID("00000000-0000-0000-0000-0000000000a1")

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.items = [
            ProxyListItem(
                id=self.existing_id,
                name="DE Residential",
                proxy_type=ProxyType.socks5,
                host="proxy.example.com",
                port=1080,
                username="user01",
                has_password=True,
                check_status=ProxyStatus.working,
                error_message=None,
                position=0,
                last_checked_at=now,
                created_at=now,
                updated_at=now,
            )
        ]
        self.deleted: set[uuid.UUID] = set()

    async def create_proxy(self, payload: Any) -> ProxyListItem:
        now = datetime.now(UTC)
        return ProxyListItem(
            id=uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
            name=payload.name,
            proxy_type=payload.proxy_type,
            host=payload.host,
            port=payload.port,
            username=payload.username or None,
            has_password=bool(payload.password),
            check_status=ProxyStatus.pending,
            error_message=None,
            position=0,
            last_checked_at=None,
            created_at=now,
            updated_at=now,
        )

    async def list_proxies(self) -> ProxyListResponse:
        return ProxyListResponse(items=self.items)

    async def get_status(self, proxy_id: uuid.UUID) -> ProxyStatusResponse:
        if proxy_id != self.existing_id:
            raise proxy_not_found()
        return ProxyStatusResponse(
            id=proxy_id,
            check_status=ProxyStatus.error,
            error_message="Прокси недоступен",
            last_checked_at=datetime.now(UTC),
        )

    async def delete_proxy(self, proxy_id: uuid.UUID) -> None:
        if proxy_id in self.deleted or proxy_id != self.existing_id:
            raise proxy_not_found()
        self.deleted.add(proxy_id)


@pytest.fixture
def fake_service() -> FakeProxyService:
    return FakeProxyService()


def _build_app(fake_service: FakeProxyService, *, with_auth: bool) -> FastAPI:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    if with_auth:
        app.dependency_overrides[deps.get_current_user] = lambda: "admin"
    app.dependency_overrides[deps.get_proxy_service] = lambda: fake_service
    return app


@pytest.fixture
def app(fake_service: FakeProxyService) -> FastAPI:
    return _build_app(fake_service, with_auth=True)


async def test_create_proxy_202_pending_has_password_no_secret(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/proxies",
            json={
                "name": "DE Residential",
                "proxy_type": "socks5",
                "host": "proxy.example.com",
                "port": 1080,
                "username": "user01",
                "password": SECRET_PASSWORD,
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["check_status"] == "pending"
    assert body["proxy_type"] == "socks5"
    assert body["has_password"] is True
    assert body["username"] == "user01"
    # Пароль (plaintext, в любом виде) не присутствует в ответе.
    assert SECRET_PASSWORD not in response.text
    assert "password" not in body
    assert "password_encrypted" not in body


async def test_create_proxy_without_password_has_password_false(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/proxies",
            json={"name": "No auth", "proxy_type": "http", "host": "host", "port": 8080},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["has_password"] is False
    assert body["username"] is None


async def test_create_proxy_invalid_type_is_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/proxies",
            json={"name": "Bad", "proxy_type": "socks4", "host": "host", "port": 8080},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


@pytest.mark.parametrize("port", [0, 65536, 99999])
async def test_create_proxy_port_out_of_range_is_422(app: FastAPI, port: int) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/proxies",
            json={"name": "Bad port", "proxy_type": "http", "host": "host", "port": port},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_create_proxy_missing_field_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Отсутствует required `host` → структурная ошибка формы → 400.
        response = await client.post(
            "/api/proxies",
            json={"name": "No host", "proxy_type": "http", "port": 8080},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_create_proxy_too_long_name_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/proxies",
            json={"name": "n" * 65, "proxy_type": "http", "host": "host", "port": 8080},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_proxies_200_no_secret(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/proxies")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["has_password"] is True
    assert body["items"][0]["check_status"] == "working"
    assert "password" not in body["items"][0]
    assert SECRET_PASSWORD not in response.text


async def test_get_status_200_and_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get(f"/api/proxies/{FakeProxyService.existing_id}/status")
        missing = await client.get("/api/proxies/00000000-0000-0000-0000-0000000000ff/status")

    assert ok.status_code == 200
    assert ok.json()["check_status"] == "error"
    assert ok.json()["error_message"] == "Прокси недоступен"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "proxy_not_found"


async def test_delete_204_then_repeat_404(app: FastAPI, fake_service: FakeProxyService) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deleted = await client.delete(f"/api/proxies/{FakeProxyService.existing_id}")
        repeated = await client.delete(f"/api/proxies/{FakeProxyService.existing_id}")

    assert deleted.status_code == 204
    assert FakeProxyService.existing_id in fake_service.deleted
    assert repeated.status_code == 404
    assert repeated.json()["error"]["code"] == "proxy_not_found"


async def test_endpoints_require_jwt_401(fake_service: FakeProxyService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/proxies")
        created = await client.post(
            "/api/proxies",
            json={"name": "n", "proxy_type": "http", "host": "host", "port": 8080},
        )

    assert listed.status_code == 401
    assert listed.json()["error"]["code"] == "unauthorized"
    assert created.status_code == 401
