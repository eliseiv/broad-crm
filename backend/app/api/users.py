"""Роутер реестра пользователей (04-api.md#users, ADR-021). Гейт require_admin.

Пароль (plaintext) — только на вход, в ответах не возвращается.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import RequireAdmin, UserServiceDep
from app.schemas.user import (
    UserCreateRequest,
    UserListItem,
    UserListResponse,
    UserUpdateRequest,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserListResponse)
async def list_users(service: UserServiceDep, _admin: RequireAdmin) -> UserListResponse:
    """Список пользователей (username/роль/статус)."""
    return await service.list_users()


@router.post("", response_model=UserListItem, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest, service: UserServiceDep, _admin: RequireAdmin
) -> UserListItem:
    """Создаёт пользователя (username 422 / role_id 422 / уникальность 409)."""
    return await service.create_user(payload)


@router.patch("/{user_id}", response_model=UserListItem)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    service: UserServiceDep,
    _admin: RequireAdmin,
) -> UserListItem:
    """Редактирование: роль/статус/сброс пароля (username не меняется)."""
    return await service.update_user(user_id, payload)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID, service: UserServiceDep, _admin: RequireAdmin
) -> Response:
    """Удаляет пользователя (hard delete)."""
    await service.delete_user(user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
