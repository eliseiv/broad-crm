"""Контрактные тесты PATCH-роутов AI-ключей (04-api.md#patch-apiai-keysid, #patch-apiai-keysorder).

Сервис замокан через dependency_overrides (как в test_ai_keys_api). Проверяются коды и
схема HTTP-границы: PATCH /{id} 200 (`position`, полный ключ отсутствует), 404, 422
(provider вне enum), 400 (длинное name), 401 без JWT; PATCH /order 204, 400 (нет
provider), 422 (provider вне enum), маппинг доменных 404 / 422. Прецеденция кодов
(422 provider → 404 → 422 группа) проверяется на сервисном слое в
test_ai_keys_reorder_update.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.api import deps
from app.errors import ai_key_not_found, unprocessable
from app.models.ai_key import AiKeyStatus, AiProvider
from app.schemas.ai_key import AiKeyListItem, AiKeyUpdateRequest
from conftest import make_principal
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

EXISTING_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
FULL_KEY = "sk-proj-SECRET-MIDDLE-PART-9QzK"


class FakeAiKeyService:
    async def update_key(self, ai_key_id: uuid.UUID, payload: AiKeyUpdateRequest) -> AiKeyListItem:
        if ai_key_id != EXISTING_ID:
            raise ai_key_not_found()
        now = datetime.now(UTC)
        return AiKeyListItem(
            id=ai_key_id,
            name=payload.name or "OpenAI Prod",
            provider=payload.provider or AiProvider.openai,
            key_masked="sk-p…9QzK",
            check_status=AiKeyStatus.pending,
            error_message=None,
            position=3,
            last_checked_at=now,
            created_at=now,
            updated_at=now,
        )

    async def reorder_keys(self, provider: AiProvider, ids: list[uuid.UUID]) -> None:
        if any(i != EXISTING_ID for i in ids):
            raise ai_key_not_found()
        if len(ids) != 1:
            raise unprocessable("Не полная перестановка группы")


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


async def test_update_key_200_position_no_full_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            f"/api/ai-keys/{EXISTING_ID}", json={"name": "Rotated", "key": FULL_KEY}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["check_status"] == "pending"
    assert body["position"] == 3
    assert body["key_masked"] == "sk-p…9QzK"
    # Полный ключ (и секретная середина) не присутствует в ответе.
    assert FULL_KEY not in response.text
    assert "SECRET-MIDDLE-PART" not in response.text
    assert "key" not in body


async def test_update_key_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/ai-keys/00000000-0000-0000-0000-0000000000ff", json={"name": "X"}
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ai_key_not_found"


async def test_update_key_invalid_provider_is_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/ai-keys/{EXISTING_ID}", json={"provider": "gemini"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_update_key_too_long_name_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/ai-keys/{EXISTING_ID}", json={"name": "n" * 65})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_update_key_requires_jwt_401(fake_service: FakeAiKeyService) -> None:
    app = _build_app(fake_service, with_auth=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/ai-keys/{EXISTING_ID}", json={"name": "X"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_reorder_keys_204(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/ai-keys/order", json={"provider": "openai", "ids": [str(EXISTING_ID)]}
        )

    assert response.status_code == 204


async def test_reorder_keys_missing_provider_is_400(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/ai-keys/order", json={"ids": [str(EXISTING_ID)]})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


async def test_reorder_keys_invalid_provider_is_422_before_id(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # provider вне enum → 422 (до проверки существования id), даже с валидными UUID.
        response = await client.patch(
            "/api/ai-keys/order", json={"provider": "gemini", "ids": [str(EXISTING_ID)]}
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_reorder_keys_unknown_id_maps_to_404(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/ai-keys/order",
            json={"provider": "openai", "ids": ["00000000-0000-0000-0000-0000000000ff"]},
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ai_key_not_found"


async def test_reorder_keys_incomplete_group_maps_to_422(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(
            "/api/ai-keys/order",
            json={"provider": "openai", "ids": [str(EXISTING_ID), str(EXISTING_ID)]},
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"
