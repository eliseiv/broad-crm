"""Контрактные/интеграционные тесты роутера бэков (04-api.md#backends).

Сервис замокан через dependency_overrides (как в test_proxies_api). Проверяются коды и
схемы ответов: POST 202 с `BackendListItem` (check_status pending), 409 backend_code_taken
на дубль code, 422 unprocessable на невалидный домен, прецеденция 422 (домен) → 409 (код),
400 на структурную ошибку тела (нет поля / длинный code), отсутствие JWT → 401, GET список,
GET status (404 backend_not_found), DELETE 204 и повтор → 404. Секрета у бэка нет — все поля
публичны.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api import deps
from app.errors import backend_code_taken, backend_not_found, unprocessable
from app.models.service_backend import BackendStatus
from app.schemas.backend import BackendListItem, BackendListResponse, BackendStatusResponse
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


class FakeBackendService:
    existing_id = uuid.UUID("00000000-0000-0000-0000-0000000000a1")

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.items = [
            BackendListItem(
                id=self.existing_id,
                code="api-eu",
                name="API EU",
                domain="api.example.com",
                check_status=BackendStatus.working,
                error_message=None,
                position=0,
                last_checked_at=now,
                created_at=now,
                updated_at=now,
            )
        ]
        self.deleted: set[uuid.UUID] = set()

    async def create_backend(self, payload: Any) -> BackendListItem:
        # Прецеденция: невалидный домен (422) проверяется ДО уникальности кода (409).
        if " " in payload.domain or "/" in payload.domain:
            raise unprocessable(
                "Невалидный формат домена",
                details=[{"field": "domain", "message": "Невалидный формат домена"}],
            )
        if payload.code == "taken":
            raise backend_code_taken()
        now = datetime.now(UTC)
        return BackendListItem(
            id=uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
            code=payload.code,
            name=payload.name,
            domain=payload.domain,
            check_status=BackendStatus.pending,
            error_message=None,
            position=0,
            last_checked_at=None,
            created_at=now,
            updated_at=now,
        )

    async def list_backends(self) -> BackendListResponse:
        return BackendListResponse(items=self.items)

    async def get_status(self, backend_id: uuid.UUID) -> BackendStatusResponse:
        if backend_id != self.existing_id:
            raise backend_not_found()
        return BackendStatusResponse(
            id=backend_id,
            check_status=BackendStatus.error,
            error_message="Бэк недоступен",
            last_checked_at=datetime.now(UTC),
        )

    async def delete_backend(self, backend_id: uuid.UUID) -> None:
        if backend_id in self.deleted or backend_id != self.existing_id:
            raise backend_not_found()
        self.deleted.add(backend_id)


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


async def test_create_backend_202_pending(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/backends",
            json={"code": "api-eu", "name": "API EU", "domain": "api.example.com"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["check_status"] == "pending"
    assert body["code"] == "api-eu"
    assert body["name"] == "API EU"
    assert body["domain"] == "api.example.com"
    # Секрета у бэка нет — все поля публичны, ничего скрытого.
    assert "password" not in body
    assert "password_encrypted" not in body


async def test_create_backend_duplicate_code_is_409(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/backends",
            json={"code": "taken", "name": "Dup", "domain": "dup.example.com"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "backend_code_taken"


async def test_create_backend_invalid_domain_is_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/backends",
            json={"code": "api-eu", "name": "API EU", "domain": "bad domain"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_create_backend_precedence_domain_422_before_code_409(app: FastAPI) -> None:
    # Дубль code И невалидный домен вместе → 422 (домен проверяется до уникальности code).
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/backends",
            json={"code": "taken", "name": "Dup", "domain": "bad/domain"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_create_backend_missing_field_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Отсутствует required `domain` → структурная ошибка формы → 400.
        response = await client.post(
            "/api/backends",
            json={"code": "api-eu", "name": "API EU"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_create_backend_too_long_code_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/backends",
            json={"code": "c" * 65, "name": "API EU", "domain": "api.example.com"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_backends_200(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/backends")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["code"] == "api-eu"
    assert body["items"][0]["check_status"] == "working"


async def test_get_status_200_and_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get(f"/api/backends/{FakeBackendService.existing_id}/status")
        missing = await client.get("/api/backends/00000000-0000-0000-0000-0000000000ff/status")

    assert ok.status_code == 200
    assert ok.json()["check_status"] == "error"
    assert ok.json()["error_message"] == "Бэк недоступен"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "backend_not_found"


async def test_delete_204_then_repeat_404(app: FastAPI, fake_service: FakeBackendService) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deleted = await client.delete(f"/api/backends/{FakeBackendService.existing_id}")
        repeated = await client.delete(f"/api/backends/{FakeBackendService.existing_id}")

    assert deleted.status_code == 204
    assert FakeBackendService.existing_id in fake_service.deleted
    assert repeated.status_code == 404
    assert repeated.json()["error"]["code"] == "backend_not_found"


async def test_endpoints_require_jwt_401(fake_service: FakeBackendService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/backends")
        created = await client.post(
            "/api/backends",
            json={"code": "api-eu", "name": "API EU", "domain": "api.example.com"},
        )

    assert listed.status_code == 401
    assert listed.json()["error"]["code"] == "unauthorized"
    assert created.status_code == 401
