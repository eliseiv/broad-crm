"""Контрактные тесты PATCH-роутов бэков (04-api.md#patch-apibackendsid, #patch-apibackendsorder).

Сервис замокан через dependency_overrides (как в test_proxies_patch_api). Проверяются коды
и схема HTTP-границы: PATCH /{id} 200 (`BackendListItem`), 404, 422 (невалидный домен), 409
(code занят другим), 400 (длинный code), 401 без JWT; PATCH /order 204, 400 (битое тело),
маппинг доменных 404 (несуществующий id) / 422 (неполная перестановка). Прецеденция кодов
reorder проверяется на сервисном слое в test_backend_service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.api import deps
from app.errors import backend_code_taken, backend_not_found, unprocessable
from app.models.service_backend import BackendStatus
from app.schemas.backend import BackendListItem, BackendUpdateRequest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

EXISTING_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


class FakeBackendService:
    async def update_backend(
        self, backend_id: uuid.UUID, payload: BackendUpdateRequest
    ) -> BackendListItem:
        if backend_id != EXISTING_ID:
            raise backend_not_found()
        if payload.domain is not None and (" " in payload.domain or "/" in payload.domain):
            raise unprocessable(
                "Невалидный формат домена",
                details=[{"field": "domain", "message": "Невалидный формат домена"}],
            )
        if payload.code == "taken":
            raise backend_code_taken()
        now = datetime.now(UTC)
        return BackendListItem(
            id=backend_id,
            code=payload.code or "api-eu",
            name=payload.name or "API EU",
            domain=payload.domain or "api.example.com",
            check_status=BackendStatus.pending,
            error_message=None,
            position=3,
            last_checked_at=now,
            created_at=now,
            updated_at=now,
        )

    async def reorder_backends(self, ids: list[uuid.UUID]) -> None:
        for backend_id in ids:
            if backend_id != EXISTING_ID:
                raise backend_not_found()
        if len(ids) != 1:
            raise unprocessable("Список не является полной перестановкой бэков")


@pytest.fixture
def fake_service() -> FakeBackendService:
    return FakeBackendService()


def _build_app(fake_service: FakeBackendService, *, with_auth: bool) -> FastAPI:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    if with_auth:
        app.dependency_overrides[deps.get_current_user] = lambda: "admin"
    app.dependency_overrides[deps.get_backend_service] = lambda: fake_service
    return app


@pytest.fixture
def app(fake_service: FakeBackendService) -> FastAPI:
    return _build_app(fake_service, with_auth=True)


async def test_update_backend_200_position(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            f"/api/backends/{EXISTING_ID}",
            json={"name": "Renamed", "domain": "new.example.com"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["check_status"] == "pending"
    assert body["position"] == 3
    assert body["domain"] == "new.example.com"


async def test_update_backend_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/backends/00000000-0000-0000-0000-0000000000ff", json={"name": "X"}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "backend_not_found"


async def test_update_backend_invalid_domain_is_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/backends/{EXISTING_ID}", json={"domain": "bad domain"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_update_backend_duplicate_code_is_409(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/backends/{EXISTING_ID}", json={"code": "taken"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "backend_code_taken"


async def test_update_backend_too_long_code_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/backends/{EXISTING_ID}", json={"code": "c" * 65})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_update_backend_requires_jwt_401(fake_service: FakeBackendService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/backends/{EXISTING_ID}", json={"name": "X"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_reorder_backends_204(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/backends/order", json={"ids": [str(EXISTING_ID)]})

    assert response.status_code == 204


async def test_reorder_backends_bad_body_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # ids не массив UUID (строки-не-UUID) → структурная ошибка формы → 400.
        response = await client.patch("/api/backends/order", json={"ids": ["not-a-uuid"]})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_reorder_backends_unknown_id_maps_to_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/backends/order", json={"ids": ["00000000-0000-0000-0000-0000000000ff"]}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "backend_not_found"


async def test_reorder_backends_incomplete_maps_to_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/backends/order", json={"ids": [str(EXISTING_ID), str(EXISTING_ID)]}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_reorder_backends_requires_jwt_401(fake_service: FakeBackendService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/backends/order", json={"ids": [str(EXISTING_ID)]})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
