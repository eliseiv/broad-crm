"""Роутер реестра бэков (04-api.md#backends). Все эндпоинты требуют JWT."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import BackendServiceDep, CurrentUser
from app.schemas.backend import (
    BackendCreateRequest,
    BackendListItem,
    BackendListResponse,
    BackendOrderRequest,
    BackendStatusResponse,
    BackendUpdateRequest,
)

router = APIRouter(prefix="/backends", tags=["backends"])


@router.get("", response_model=BackendListResponse)
async def list_backends(service: BackendServiceDep, _user: CurrentUser) -> BackendListResponse:
    """Список бэков (position ASC, created_at DESC, id)."""
    return await service.list_backends()


@router.post("", response_model=BackendListItem, status_code=status.HTTP_202_ACCEPTED)
async def create_backend(
    payload: BackendCreateRequest, service: BackendServiceDep, _user: CurrentUser
) -> BackendListItem:
    """Создаёт бэк и запускает немедленную фоновую проверку (202, check_status pending)."""
    return await service.create_backend(payload)


@router.patch("/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_backends(
    payload: BackendOrderRequest, service: BackendServiceDep, _user: CurrentUser
) -> Response:
    """Перестановка единого списка бэков (position=0..N-1)."""
    await service.reorder_backends(payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{backend_id}", response_model=BackendListItem)
async def update_backend(
    backend_id: uuid.UUID,
    payload: BackendUpdateRequest,
    service: BackendServiceDep,
    _user: CurrentUser,
) -> BackendListItem:
    """Редактирование бэка; re-check при смене `domain` (200)."""
    return await service.update_backend(backend_id, payload)


@router.get("/{backend_id}/status", response_model=BackendStatusResponse)
async def get_backend_status(
    backend_id: uuid.UUID, service: BackendServiceDep, _user: CurrentUser
) -> BackendStatusResponse:
    """Лёгкий статус проверки для polling после добавления/редактирования."""
    return await service.get_status(backend_id)


@router.delete("/{backend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backend(
    backend_id: uuid.UUID, service: BackendServiceDep, _user: CurrentUser
) -> Response:
    """Удаляет бэк из реестра (hard delete)."""
    await service.delete_backend(backend_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
