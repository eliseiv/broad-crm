"""Бизнес-логика реестра пользователей (modules/auth, 04-api.md#users, ADR-021).

Пароль хранится только как bcrypt-хэш; plaintext не возвращается/не логируется.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.identity import IdentityNameError, validate_identity_name
from app.errors import unprocessable, user_not_found, username_taken
from app.infra.passwords import hash_password
from app.logging import get_logger
from app.models.user import User
from app.repositories.role_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    UserCreateRequest,
    UserListItem,
    UserListResponse,
    UserUpdateRequest,
)

logger = get_logger(__name__)

# Политика пароля БД-пользователя (05-security.md): 8–128 символов.
_PASSWORD_MIN_LEN = 8
_PASSWORD_MAX_LEN = 128


def _validate_username(raw: str) -> str:
    """Валидирует/нормализует username; нарушение → 422 unprocessable."""
    try:
        return validate_identity_name(raw)
    except IdentityNameError as exc:
        raise unprocessable(
            "Недопустимое имя пользователя",
            details=[{"field": "username", "message": str(exc)}],
        ) from exc


def _validate_password_reset(password: str) -> None:
    """Проверяет длину пароля при сбросе (PATCH); нарушение/`""` → 422 unprocessable."""
    if not (_PASSWORD_MIN_LEN <= len(password) <= _PASSWORD_MAX_LEN):
        raise unprocessable(
            "Пароль должен быть длиной 8–128 символов",
            details=[{"field": "password", "message": "Недопустимая длина пароля"}],
        )


class UserService:
    """CRUD реестра пользователей: валидация username/role/пароля, bcrypt-хэш."""

    def __init__(self, *, users: UserRepository, roles: RoleRepository) -> None:
        self._users = users
        self._roles = roles

    async def list_users(self) -> UserListResponse:
        """Список пользователей (created_at ASC, id) с денормализованным именем роли."""
        users = await self._users.list_all()
        return UserListResponse(items=[self._to_item(user) for user in users])

    async def create_user(self, payload: UserCreateRequest) -> UserListItem:
        """Создаёт пользователя. Прецеденция: username-формат (422) →
        существование role_id (422) → уникальность username (409)."""
        username = _validate_username(payload.username)

        role = await self._roles.get_by_id(payload.role_id)
        if role is None:
            raise unprocessable(
                "Роль не найдена",
                details=[{"field": "role_id", "message": "Роль не существует"}],
            )

        if await self._users.exists_by_username(username):
            raise username_taken()

        try:
            user = await self._users.create(
                username=username,
                password_hash=hash_password(payload.password),
                role_id=payload.role_id,
            )
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_create_conflict")
            raise username_taken() from exc

        # Роль подгружена eager (lazy="joined") после refresh в create.
        logger.info("user_created", user_id=str(user.id))
        return self._to_item(user)

    async def update_user(self, user_id: uuid.UUID, payload: UserUpdateRequest) -> UserListItem:
        """Редактирует роль/статус/пароль. 404 → 422 (role_id/password). username
        не редактируется. Деактивация аннулирует JWT на следующем запросе."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise user_not_found()

        fields_set = payload.model_fields_set

        if "role_id" in fields_set and payload.role_id is not None:
            role = await self._roles.get_by_id(payload.role_id)
            if role is None:
                raise unprocessable(
                    "Роль не найдена",
                    details=[{"field": "role_id", "message": "Роль не существует"}],
                )
            # Присваиваем связь (а не только FK), чтобы `user.role` не остался
            # устаревшим (иначе role_name в ответе показал бы старую роль).
            user.role = role

        if "password" in fields_set and payload.password is not None:
            _validate_password_reset(payload.password)
            user.password_hash = hash_password(payload.password)

        if "is_active" in fields_set and payload.is_active is not None:
            user.is_active = payload.is_active

        await self._users.session.commit()
        await self._users.session.refresh(user)

        logger.info("user_updated", user_id=str(user_id))
        return self._to_item(user)

    async def delete_user(self, user_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404 user_not_found."""
        deleted = await self._users.delete_by_id(user_id)
        if not deleted:
            raise user_not_found()
        await self._users.session.commit()
        logger.info("user_deleted", user_id=str(user_id))

    @staticmethod
    def _to_item(user: User) -> UserListItem:
        """Собирает элемент ответа (пароль никогда не включается)."""
        return UserListItem(
            id=user.id,
            username=user.username,
            role_id=user.role_id,
            role_name=user.role.name,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
