"""Роутер реестра ролей (04-api.md#roles, ADR-021). Гейт require_admin."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import RequireAdmin, RoleServiceDep
from app.schemas.role import (
    RoleCreateRequest,
    RoleListItem,
    RoleListResponse,
    RoleUpdateRequest,
)

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("", response_model=RoleListResponse)
async def list_roles(service: RoleServiceDep, _admin: RequireAdmin) -> RoleListResponse:
    """Список ролей с правами-матрицей."""
    return await service.list_roles()


@router.post("", response_model=RoleListItem, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreateRequest, service: RoleServiceDep, _admin: RequireAdmin
) -> RoleListItem:
    """Создаёт роль (name 422 / permissions вне каталога 422 / уникальность 409)."""
    return await service.create_role(payload)


@router.patch("/{role_id}", response_model=RoleListItem)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    service: RoleServiceDep,
    _admin: RequireAdmin,
) -> RoleListItem:
    """Редактирование роли (name и/или permissions); правки прав без пере-логина."""
    return await service.update_role(role_id, payload)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID, service: RoleServiceDep, _admin: RequireAdmin
) -> Response:
    """Удаляет роль (hard delete); роль с носителями → 409 role_in_use."""
    await service.delete_role(role_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
