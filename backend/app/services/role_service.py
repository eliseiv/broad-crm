"""Бизнес-логика реестра ролей (modules/auth, 04-api.md#roles, ADR-021)."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.identity import IdentityNameError, validate_identity_name
from app.domain.permissions import PermissionsValidationError, validate_permissions
from app.errors import role_in_use, role_name_taken, role_not_found, unprocessable
from app.logging import get_logger
from app.models.role import Role
from app.repositories.role_repository import RoleRepository
from app.schemas.role import (
    RoleCreateRequest,
    RoleListItem,
    RoleListResponse,
    RoleUpdateRequest,
)

logger = get_logger(__name__)


def _validate_name(raw: str) -> str:
    """Валидирует/нормализует имя роли; нарушение → 422 unprocessable."""
    try:
        return validate_identity_name(raw)
    except IdentityNameError as exc:
        raise unprocessable(
            "Недопустимое имя роли",
            details=[{"field": "name", "message": str(exc)}],
        ) from exc


def _validate_permissions(permissions: dict[str, list[str]]) -> None:
    """Валидирует права против каталога; нарушение → 422 unprocessable."""
    try:
        validate_permissions(permissions)
    except PermissionsValidationError as exc:
        raise unprocessable(
            "Недопустимые права роли",
            details=[{"field": "permissions", "message": str(exc)}],
        ) from exc


class RoleService:
    """CRUD реестра ролей с валидацией имени/прав против каталога."""

    def __init__(self, repository: RoleRepository) -> None:
        self._repo = repository

    async def list_roles(self) -> RoleListResponse:
        """Список ролей (created_at ASC, id)."""
        roles = await self._repo.list_all()
        return RoleListResponse(items=[self._to_item(role) for role in roles])

    async def create_role(self, payload: RoleCreateRequest) -> RoleListItem:
        """Создаёт роль. Прецеденция: схемная валидация (422 name/permissions) →
        уникальность name (409 role_name_taken)."""
        name = _validate_name(payload.name)
        _validate_permissions(payload.permissions)

        if await self._repo.exists_by_name(name):
            raise role_name_taken()

        try:
            role = await self._repo.create(name=name, permissions=payload.permissions)
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("role_create_conflict", name=name)
            raise role_name_taken() from exc

        logger.info("role_created", role_id=str(role.id))
        return self._to_item(role)

    async def update_role(self, role_id: uuid.UUID, payload: RoleUpdateRequest) -> RoleListItem:
        """Редактирует роль (name и/или permissions). 404 → 422 (name/permissions) →
        409 (name занят). Правки прав применяются без пере-логина носителей."""
        role = await self._repo.get_by_id(role_id)
        if role is None:
            raise role_not_found()

        fields_set = payload.model_fields_set

        if "name" in fields_set and payload.name is not None:
            new_name = _validate_name(payload.name)
            if new_name != role.name and await self._repo.exists_by_name(
                new_name, exclude_id=role.id
            ):
                raise role_name_taken()
            role.name = new_name

        if "permissions" in fields_set and payload.permissions is not None:
            _validate_permissions(payload.permissions)
            role.permissions = payload.permissions

        try:
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("role_update_conflict", role_id=str(role_id))
            raise role_name_taken() from exc
        await self._repo.session.refresh(role)

        logger.info("role_updated", role_id=str(role_id))
        return self._to_item(role)

    async def delete_role(self, role_id: uuid.UUID) -> None:
        """Hard-delete. Запрещено удалять роль с носителями → 409 role_in_use."""
        role = await self._repo.get_by_id(role_id)
        if role is None:
            raise role_not_found()
        if await self._repo.is_in_use(role_id):
            raise role_in_use()

        try:
            await self._repo.delete_by_id(role_id)
            await self._repo.session.commit()
        except IntegrityError as exc:
            # Гонка: пользователь назначен на роль между проверкой и удалением
            # (ON DELETE RESTRICT). Детерминированный 409 role_in_use.
            await self._repo.session.rollback()
            logger.info("role_delete_in_use", role_id=str(role_id))
            raise role_in_use() from exc

        logger.info("role_deleted", role_id=str(role_id))

    @staticmethod
    def _to_item(role: Role) -> RoleListItem:
        """Собирает элемент ответа (права как есть, уже валидированы против каталога)."""
        return RoleListItem(
            id=role.id,
            name=role.name,
            permissions=dict(role.permissions),
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
