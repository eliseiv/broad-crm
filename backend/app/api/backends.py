"""Роутер реестра бэков (04-api.md#backends). RBAC-гейт require(backends, ...)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import BackendServiceDep, Principal, require
from app.infra.audit import log_secret_revealed
from app.schemas.backend import (
    BackendCreateRequest,
    BackendListItem,
    BackendListResponse,
    BackendOrderRequest,
    BackendStatusResponse,
    BackendUpdateRequest,
)
from app.schemas.secret import SecretRevealResponse

router = APIRouter(prefix="/backends", tags=["backends"])

ViewDep = Annotated[Principal, Depends(require("backends", "view"))]
CreateDep = Annotated[Principal, Depends(require("backends", "create"))]
EditDep = Annotated[Principal, Depends(require("backends", "edit"))]
DeleteDep = Annotated[Principal, Depends(require("backends", "delete"))]


@router.get("", response_model=BackendListResponse)
async def list_backends(service: BackendServiceDep, _p: ViewDep) -> BackendListResponse:
    """Список бэков (position ASC, created_at DESC, id)."""
    return await service.list_backends()


@router.post("", response_model=BackendListItem, status_code=status.HTTP_202_ACCEPTED)
async def create_backend(
    payload: BackendCreateRequest, service: BackendServiceDep, _p: CreateDep
) -> BackendListItem:
    """Создаёт бэк и запускает немедленную фоновую проверку (202, check_status pending)."""
    return await service.create_backend(payload)


@router.patch("/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_backends(
    payload: BackendOrderRequest, service: BackendServiceDep, _p: EditDep
) -> Response:
    """Перестановка единого списка бэков (position=0..N-1)."""
    await service.reorder_backends(payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{backend_id}", response_model=BackendListItem)
async def update_backend(
    backend_id: uuid.UUID,
    payload: BackendUpdateRequest,
    service: BackendServiceDep,
    _p: EditDep,
) -> BackendListItem:
    """Редактирование бэка; re-check при смене `domain` (200)."""
    return await service.update_backend(backend_id, payload)


@router.get("/{backend_id}/status", response_model=BackendStatusResponse)
async def get_backend_status(
    backend_id: uuid.UUID, service: BackendServiceDep, _p: ViewDep
) -> BackendStatusResponse:
    """Лёгкий статус проверки для polling после добавления/редактирования."""
    return await service.get_status(backend_id)


@router.get("/{backend_id}/api-key", response_model=SecretRevealResponse)
async def reveal_backend_api_key(
    backend_id: uuid.UUID,
    service: BackendServiceDep,
    principal: EditDep,
    response: Response,
) -> SecretRevealResponse:
    """On-demand reveal API KEY бэка (ADR-040, require backends:edit).

    Секрет — в теле ответа (не в URL); `Cache-Control: no-store` исключает кэш.
    Успешный reveal порождает аудит-лог `secret_revealed` (без значения).
    """
    value = await service.reveal_api_key(backend_id)
    response.headers["Cache-Control"] = "no-store"
    log_secret_revealed(principal, resource_type="backend", resource_id=str(backend_id))
    return SecretRevealResponse(value=value)


@router.get("/{backend_id}/admin-api-key", response_model=SecretRevealResponse)
async def reveal_backend_admin_api_key(
    backend_id: uuid.UUID,
    service: BackendServiceDep,
    principal: EditDep,
    response: Response,
) -> SecretRevealResponse:
    """On-demand reveal ADMIN API KEY бэка (ADR-040, require backends:edit).

    Секрет — в теле ответа (не в URL); `Cache-Control: no-store`. Успех → аудит-лог
    `secret_revealed` (без значения).
    """
    value = await service.reveal_admin_api_key(backend_id)
    response.headers["Cache-Control"] = "no-store"
    log_secret_revealed(principal, resource_type="backend", resource_id=str(backend_id))
    return SecretRevealResponse(value=value)


@router.delete("/{backend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backend(
    backend_id: uuid.UUID, service: BackendServiceDep, _p: DeleteDep
) -> Response:
    """Удаляет бэк из реестра (hard delete)."""
    await service.delete_backend(backend_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
