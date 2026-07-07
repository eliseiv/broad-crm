"""Контрактные тесты PATCH-роутов серверов (04-api.md#patch-apiserversid, #patch-apiserversorder).

Сервис замокан через dependency_overrides (как в test_servers_api). Проверяются коды
и схема HTTP-границы: PATCH /{id} 200 с `position` (меняется только name), 404, 400
(пустое/длинное name), 401 без JWT; PATCH /order 204, 400 (битое тело — не UUID), а
также маппинг доменных ошибок сервиса (404 / 422). Прецеденция кодов (404 до 422)
проверяется на сервисном слое в test_servers_reorder_update.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.api import deps
from app.errors import server_not_found, unprocessable
from app.models.server import ProvisionStatus
from app.schemas.server import ServerSummaryResponse, ServerUpdateRequest
from conftest import make_principal
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

EXISTING_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")


class FakeServersService:
    async def update_server(
        self, server_id: uuid.UUID, payload: ServerUpdateRequest
    ) -> ServerSummaryResponse:
        if server_id != EXISTING_ID:
            raise server_not_found()
        now = datetime.now(UTC)
        return ServerSummaryResponse(
            id=server_id,
            name=payload.name,
            ip="10.0.0.12",
            exporter_port=9100,
            provision_status=ProvisionStatus.online,
            position=2,
            created_at=now,
            updated_at=now,
        )

    async def reorder_servers(self, ids: list[uuid.UUID]) -> None:
        if any(i != EXISTING_ID for i in ids):
            raise server_not_found()
        if len(ids) != 1:
            raise unprocessable("Не полная перестановка")


@pytest.fixture
def fake_service() -> FakeServersService:
    return FakeServersService()


def _build_app(fake_service: FakeServersService, *, with_auth: bool) -> FastAPI:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    if with_auth:
        app.dependency_overrides[deps.get_current_principal] = lambda: make_principal()
    app.dependency_overrides[deps.get_server_service] = lambda: fake_service
    return app


@pytest.fixture
def app(fake_service: FakeServersService) -> FastAPI:
    return _build_app(fake_service, with_auth=True)


async def test_update_server_200_only_name_with_position(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/servers/{EXISTING_ID}", json={"name": "Renamed"})

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["ip"] == "10.0.0.12"
    assert body["position"] == 2


async def test_update_server_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/servers/00000000-0000-0000-0000-0000000009ff", json={"name": "X"}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "server_not_found"


async def test_update_server_empty_name_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/servers/{EXISTING_ID}", json={"name": ""})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_update_server_too_long_name_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/servers/{EXISTING_ID}", json={"name": "n" * 65})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_update_server_requires_jwt_401(fake_service: FakeServersService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/servers/{EXISTING_ID}", json={"name": "X"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_reorder_servers_204(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/servers/order", json={"ids": [str(EXISTING_ID)]})

    assert response.status_code == 204


async def test_reorder_servers_malformed_body_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/servers/order", json={"ids": ["not-a-uuid"]})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_reorder_servers_unknown_id_maps_to_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/servers/order", json={"ids": ["00000000-0000-0000-0000-0000000009ff"]}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "server_not_found"


async def test_reorder_servers_incomplete_maps_to_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Все id существуют, но список не полная перестановка (fake отдаёт 422).
        response = await client.patch(
            "/api/servers/order", json={"ids": [str(EXISTING_ID), str(EXISTING_ID)]}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"
