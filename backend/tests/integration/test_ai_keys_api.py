"""Контрактные/интеграционные тесты роутера AI-ключей (04-api.md#ai-keys).

Сервис замокан через dependency_overrides (как в test_servers_api). Проверяются коды
и схемы ответов: POST 202 с `AiKeyListItem` (маска, без полного ключа), невалидный
provider → 422, отсутствие JWT → 401, GET список, GET status (404 ai_key_not_found),
DELETE 204 и повтор → 404. Полный ключ не присутствует ни в одном ответе.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api import deps
from app.errors import ai_key_not_found
from app.models.ai_key import AiKeyStatus, AiProvider
from app.schemas.ai_key import (
    AiKeyListItem,
    AiKeyListResponse,
    AiKeyStatusResponse,
)
from conftest import make_principal
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

FULL_KEY = "sk-proj-SECRET-MIDDLE-PART-bA3T"


class FakeAiKeyService:
    existing_id = uuid.UUID("00000000-0000-0000-0000-0000000000a1")

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.items = [
            AiKeyListItem(
                id=self.existing_id,
                name="OpenAI Prod",
                provider=AiProvider.openai,
                key_masked="sk-p…bA3T",
                check_status=AiKeyStatus.working,
                error_message=None,
                position=0,
                last_checked_at=now,
                created_at=now,
                updated_at=now,
            )
        ]
        self.deleted: set[uuid.UUID] = set()

    async def create_key(self, payload: Any) -> AiKeyListItem:
        now = datetime.now(UTC)
        return AiKeyListItem(
            id=uuid.UUID("00000000-0000-0000-0000-0000000000b2"),
            name=payload.name,
            provider=payload.provider,
            key_masked="sk-p…bA3T",
            check_status=AiKeyStatus.pending,
            error_message=None,
            position=0,
            last_checked_at=None,
            created_at=now,
            updated_at=now,
        )

    async def list_keys(self) -> AiKeyListResponse:
        return AiKeyListResponse(items=self.items)

    async def get_status(self, ai_key_id: uuid.UUID) -> AiKeyStatusResponse:
        if ai_key_id != self.existing_id:
            raise ai_key_not_found()
        return AiKeyStatusResponse(
            id=ai_key_id,
            check_status=AiKeyStatus.error,
            error_message="Недостаточно средств",
            last_checked_at=datetime.now(UTC),
        )

    async def delete_key(self, ai_key_id: uuid.UUID) -> None:
        if ai_key_id in self.deleted or ai_key_id != self.existing_id:
            raise ai_key_not_found()
        self.deleted.add(ai_key_id)


@pytest.fixture
def fake_service() -> FakeAiKeyService:
    return FakeAiKeyService()


def _build_app(fake_service: FakeAiKeyService, *, with_auth: bool) -> FastAPI:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    if with_auth:
        app.dependency_overrides[deps.get_current_principal] = lambda: make_principal()
    app.dependency_overrides[deps.get_ai_key_service] = lambda: fake_service
    return app


@pytest.fixture
def app(fake_service: FakeAiKeyService) -> FastAPI:
    return _build_app(fake_service, with_auth=True)


async def test_create_key_202_pending_masked_no_full_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai-keys",
            json={"name": "OpenAI Prod", "provider": "openai", "key": FULL_KEY},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["check_status"] == "pending"
    assert body["provider"] == "openai"
    assert body["key_masked"] == "sk-p…bA3T"
    # Полный ключ (и его секретная середина) не присутствует в ответе.
    assert FULL_KEY not in response.text
    assert "SECRET-MIDDLE-PART" not in response.text
    assert "key_encrypted" not in body
    assert "key" not in body


async def test_create_key_invalid_provider_is_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai-keys",
            json={"name": "Bad", "provider": "gemini", "key": "some-key-12345678"},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_create_key_missing_field_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ai-keys",
            json={"provider": "openai", "key": "some-key-12345678"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_list_keys_200_masked_no_full_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/ai-keys")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["key_masked"] == "sk-p…bA3T"
    assert body["items"][0]["check_status"] == "working"
    assert FULL_KEY not in response.text


async def test_get_status_200_and_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.get(f"/api/ai-keys/{FakeAiKeyService.existing_id}/status")
        missing = await client.get("/api/ai-keys/00000000-0000-0000-0000-0000000000ff/status")

    assert ok.status_code == 200
    assert ok.json()["check_status"] == "error"
    assert ok.json()["error_message"] == "Недостаточно средств"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ai_key_not_found"


async def test_delete_204_then_repeat_404(app: FastAPI, fake_service: FakeAiKeyService) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deleted = await client.delete(f"/api/ai-keys/{FakeAiKeyService.existing_id}")
        repeated = await client.delete(f"/api/ai-keys/{FakeAiKeyService.existing_id}")

    assert deleted.status_code == 204
    assert FakeAiKeyService.existing_id in fake_service.deleted
    assert repeated.status_code == 404
    assert repeated.json()["error"]["code"] == "ai_key_not_found"


async def test_endpoints_require_jwt_401(fake_service: FakeAiKeyService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/ai-keys")
        created = await client.post(
            "/api/ai-keys",
            json={"name": "n", "provider": "openai", "key": "some-key-12345678"},
        )

    assert listed.status_code == 401
    assert listed.json()["error"]["code"] == "unauthorized"
    assert created.status_code == 401
