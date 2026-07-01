"""Роутер реестра AI-ключей (04-api.md#ai-keys). Все эндпоинты требуют JWT."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import AiKeyServiceDep, CurrentUser
from app.schemas.ai_key import (
    AiKeyCreateRequest,
    AiKeyListItem,
    AiKeyListResponse,
    AiKeyStatusResponse,
)

router = APIRouter(prefix="/ai-keys", tags=["ai-keys"])


@router.get("", response_model=AiKeyListResponse)
async def list_ai_keys(service: AiKeyServiceDep, _user: CurrentUser) -> AiKeyListResponse:
    """Список AI-ключей (created_at DESC). Полный ключ не раскрывается."""
    return await service.list_keys()


@router.post("", response_model=AiKeyListItem, status_code=status.HTTP_202_ACCEPTED)
async def create_ai_key(
    payload: AiKeyCreateRequest, service: AiKeyServiceDep, _user: CurrentUser
) -> AiKeyListItem:
    """Создаёт ключ и запускает немедленную фоновую проверку (202, check_status pending)."""
    return await service.create_key(payload)


@router.get("/{ai_key_id}/status", response_model=AiKeyStatusResponse)
async def get_ai_key_status(
    ai_key_id: uuid.UUID, service: AiKeyServiceDep, _user: CurrentUser
) -> AiKeyStatusResponse:
    """Лёгкий статус проверки для polling после добавления."""
    return await service.get_status(ai_key_id)


@router.delete("/{ai_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_key(
    ai_key_id: uuid.UUID, service: AiKeyServiceDep, _user: CurrentUser
) -> Response:
    """Удаляет ключ из реестра (hard delete)."""
    await service.delete_key(ai_key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
