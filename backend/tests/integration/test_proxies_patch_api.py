"""Контрактные тесты PATCH-роутов прокси (04-api.md#patch-apiproxiesid, #patch-apiproxiesorder).

Сервис замокан через dependency_overrides (как в test_ai_keys_patch_api). Проверяются
коды и схема HTTP-границы: PATCH /{id} 200 (`ProxyListItem`, пароль отсутствует), 404,
422 (proxy_type вне enum / port вне диапазона), 400 (длинное name), 401 без JWT;
PATCH /order 204, 400 (битое тело), маппинг доменных 404 (несуществующий id) / 422
(неполная перестановка). Прецеденция кодов reorder проверяется на сервисном слое в
test_proxy_service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.api import deps
from app.errors import proxy_not_found, unprocessable
from app.models.proxy import ProxyStatus, ProxyType
from app.schemas.proxy import ProxyListItem, ProxyUpdateRequest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

EXISTING_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
SECRET_PASSWORD = "n3w-s3cr3t-PROXY-PASS"


class FakeProxyService:
    async def update_proxy(self, proxy_id: uuid.UUID, payload: ProxyUpdateRequest) -> ProxyListItem:
        if proxy_id != EXISTING_ID:
            raise proxy_not_found()
        now = datetime.now(UTC)
        return ProxyListItem(
            id=proxy_id,
            name=payload.name or "DE Residential",
            proxy_type=payload.proxy_type or ProxyType.socks5,
            host=payload.host or "proxy.example.com",
            port=payload.port or 1080,
            username="user01",
            has_password=True,
            check_status=ProxyStatus.pending,
            error_message=None,
            position=3,
            last_checked_at=now,
            created_at=now,
            updated_at=now,
        )

    async def reorder_proxies(self, ids: list[uuid.UUID]) -> None:
        for proxy_id in ids:
            if proxy_id != EXISTING_ID:
                raise proxy_not_found()
        if len(ids) != 1:
            raise unprocessable("Список не является полной перестановкой прокси")


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


async def test_update_proxy_200_position_no_secret(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            f"/api/proxies/{EXISTING_ID}",
            json={"name": "Rotated", "password": SECRET_PASSWORD},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["check_status"] == "pending"
    assert body["position"] == 3
    assert body["has_password"] is True
    # Пароль (plaintext) не присутствует в ответе.
    assert SECRET_PASSWORD not in response.text
    assert "password" not in body


async def test_update_proxy_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/proxies/00000000-0000-0000-0000-0000000000ff", json={"name": "X"}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "proxy_not_found"


async def test_update_proxy_invalid_type_is_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/proxies/{EXISTING_ID}", json={"proxy_type": "socks4"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_update_proxy_port_out_of_range_is_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/proxies/{EXISTING_ID}", json={"port": 70000})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_update_proxy_too_long_name_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/proxies/{EXISTING_ID}", json={"name": "n" * 65})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_update_proxy_requires_jwt_401(fake_service: FakeProxyService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/proxies/{EXISTING_ID}", json={"name": "X"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_reorder_proxies_204(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/proxies/order", json={"ids": [str(EXISTING_ID)]})

    assert response.status_code == 204


async def test_reorder_proxies_bad_body_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # ids не массив UUID (строки-не-UUID) → структурная ошибка формы → 400.
        response = await client.patch("/api/proxies/order", json={"ids": ["not-a-uuid"]})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_reorder_proxies_unknown_id_maps_to_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/proxies/order", json={"ids": ["00000000-0000-0000-0000-0000000000ff"]}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "proxy_not_found"


async def test_reorder_proxies_incomplete_maps_to_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/proxies/order", json={"ids": [str(EXISTING_ID), str(EXISTING_ID)]}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_reorder_proxies_requires_jwt_401(fake_service: FakeProxyService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/proxies/order", json={"ids": [str(EXISTING_ID)]})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
