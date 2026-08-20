"""Роутер реестра пользователей (04-api.md#users, ADR-021/022/025).

Гейт — матрица `users:*` (страница вошла в каталог прав; прежде был `require_admin`):
иначе выдать доступ к реестру не-админской роли было невозможно. Security-инвариант
эскалации реализует `UserService`: непривилегированный актор (`is_admin_level = false`)
не назначает роль с правами шире собственного union и не трогает admin-level
пользователя. Пароль (plaintext) — только на вход, в ответах не возвращается.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import Principal, UserServiceDep, require_admin_or
from app.domain.permissions import is_admin_level
from app.schemas.user import (
    UserCreateRequest,
    UserListItem,
    UserListResponse,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["users"])


def _is_privileged(principal: Principal) -> bool:
    """Привилегированный актор: `is_admin_level` (ADR-076 §4, зеркало роутера ролей)."""
    return is_admin_level(principal)


@router.get("", response_model=UserListResponse)
async def list_users(
    service: UserServiceDep,
    _principal: Annotated[Principal, Depends(require_admin_or("users", "view"))],
) -> UserListResponse:
    """Список пользователей (ФИО/роли/статус)."""
    return await service.list_users()


@router.post("", response_model=UserListItem, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    service: UserServiceDep,
    principal: Annotated[Principal, Depends(require_admin_or("users", "create"))],
) -> UserListItem:
    """Создаёт пользователя (ФИО/telegram 422 / role_ids 422 → 403 → уникальность 409)."""
    return await service.create_user(
        payload,
        actor_permissions=principal.permissions,
        actor_privileged=_is_privileged(principal),
    )


@router.patch("/{user_id}", response_model=UserListItem)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    service: UserServiceDep,
    principal: Annotated[Principal, Depends(require_admin_or("users", "edit"))],
) -> UserListItem:
    """Редактирование: ФИО/роли/статус/пароль (username не меняется)."""
    return await service.update_user(
        user_id,
        payload,
        actor_permissions=principal.permissions,
        actor_privileged=_is_privileged(principal),
    )


@router.post("/{user_id}/reset-password", response_model=UserListItem)
async def reset_user_password(
    user_id: uuid.UUID,
    service: UserServiceDep,
    principal: Annotated[Principal, Depends(require_admin_or("users", "edit"))],
) -> UserListItem:
    """Сброс пароля к «открытому первому входу» (ADR-025): `password_hash → NULL`.

    Пользователь при следующем входе задаёт новый пароль сам — тот же сценарий, что
    у нового сотрудника. Новый пароль оператору НЕ показывается и не генерируется:
    иначе его пришлось бы передавать по незащищённому каналу.
    """
    return await service.reset_password(user_id, actor_privileged=_is_privileged(principal))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    service: UserServiceDep,
    principal: Annotated[Principal, Depends(require_admin_or("users", "delete"))],
) -> Response:
    """Удаляет пользователя (hard delete)."""
    await service.delete_user(user_id, actor_privileged=_is_privileged(principal))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
