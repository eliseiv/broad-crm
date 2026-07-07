"""Роутер реестра AI-ключей (04-api.md#ai-keys). RBAC-гейт require(ai-keys, ...)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import AiKeyServiceDep, Principal, require
from app.schemas.ai_key import (
    AiKeyCreateRequest,
    AiKeyListItem,
    AiKeyListResponse,
    AiKeyOrderRequest,
    AiKeyStatusResponse,
    AiKeyUpdateRequest,
)

router = APIRouter(prefix="/ai-keys", tags=["ai-keys"])

ViewDep = Annotated[Principal, Depends(require("ai-keys", "view"))]
CreateDep = Annotated[Principal, Depends(require("ai-keys", "create"))]
EditDep = Annotated[Principal, Depends(require("ai-keys", "edit"))]
DeleteDep = Annotated[Principal, Depends(require("ai-keys", "delete"))]


@router.get("", response_model=AiKeyListResponse)
async def list_ai_keys(service: AiKeyServiceDep, _p: ViewDep) -> AiKeyListResponse:
    """Список AI-ключей (position ASC, created_at DESC, id). Полный ключ не раскрывается."""
    return await service.list_keys()


@router.post("", response_model=AiKeyListItem, status_code=status.HTTP_202_ACCEPTED)
async def create_ai_key(
    payload: AiKeyCreateRequest, service: AiKeyServiceDep, _p: CreateDep
) -> AiKeyListItem:
    """Создаёт ключ и запускает немедленную фоновую проверку (202, check_status pending)."""
    return await service.create_key(payload)


@router.patch("/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_ai_keys(
    payload: AiKeyOrderRequest, service: AiKeyServiceDep, _p: EditDep
) -> Response:
    """Перестановка ключей внутри провайдер-группы (position=0..M-1)."""
    await service.reorder_keys(payload.provider, payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{ai_key_id}", response_model=AiKeyListItem)
async def update_ai_key(
    ai_key_id: uuid.UUID,
    payload: AiKeyUpdateRequest,
    service: AiKeyServiceDep,
    _p: EditDep,
) -> AiKeyListItem:
    """Редактирование ключа (name/provider/key); re-check при смене provider/key (200)."""
    return await service.update_key(ai_key_id, payload)


@router.get("/{ai_key_id}/status", response_model=AiKeyStatusResponse)
async def get_ai_key_status(
    ai_key_id: uuid.UUID, service: AiKeyServiceDep, _p: ViewDep
) -> AiKeyStatusResponse:
    """Лёгкий статус проверки для polling после добавления."""
    return await service.get_status(ai_key_id)


@router.delete("/{ai_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ai_key(ai_key_id: uuid.UUID, service: AiKeyServiceDep, _p: DeleteDep) -> Response:
    """Удаляет ключ из реестра (hard delete)."""
    await service.delete_key(ai_key_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
