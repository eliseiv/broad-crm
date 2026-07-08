"""Роутер реестра ролей (04-api.md#roles, ADR-021/022). Гейт матрицы `roles:*`.

Со Спринта A `require_admin` заменён на `require("roles", <action>)` (ADR-022). Backend
реализует security-инвариант эскалации: актор (его права + признак привилегированности)
передаётся сервису после прохождения гейта.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import Principal, RoleServiceDep, require
from app.schemas.role import (
    RoleCreateRequest,
    RoleListItem,
    RoleListResponse,
    RoleUpdateRequest,
)

router = APIRouter(prefix="/roles", tags=["roles"])


def _is_privileged(principal: Principal) -> bool:
    """Привилегированный актор (полный каталог): супер-админ ИЛИ роль `admin`."""
    return principal.is_superadmin or principal.role == "admin"


@router.get("", response_model=RoleListResponse)
async def list_roles(
    service: RoleServiceDep,
    _principal: Annotated[Principal, Depends(require("roles", "view"))],
) -> RoleListResponse:
    """Список ролей с правами-матрицей и `user_count`."""
    return await service.list_roles()


@router.post("", response_model=RoleListItem, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreateRequest,
    service: RoleServiceDep,
    principal: Annotated[Principal, Depends(require("roles", "create"))],
) -> RoleListItem:
    """Создаёт роль (name/permissions 422 → эскалация 403 → уникальность 409)."""
    return await service.create_role(
        payload,
        actor_permissions=principal.permissions,
        actor_privileged=_is_privileged(principal),
    )


@router.patch("/{role_id}", response_model=RoleListItem)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    service: RoleServiceDep,
    principal: Annotated[Principal, Depends(require("roles", "edit"))],
) -> RoleListItem:
    """Редактирование роли (name и/или permissions); защита `admin` + subset-инвариант."""
    return await service.update_role(
        role_id,
        payload,
        actor_permissions=principal.permissions,
        actor_privileged=_is_privileged(principal),
    )


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: uuid.UUID,
    service: RoleServiceDep,
    principal: Annotated[Principal, Depends(require("roles", "delete"))],
) -> Response:
    """Удаляет роль (роль `admin` — только привилегированный; носители → 409 role_in_use)."""
    await service.delete_role(role_id, actor_privileged=_is_privileged(principal))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
