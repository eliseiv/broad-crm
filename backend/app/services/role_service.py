"""Бизнес-логика реестра ролей (modules/auth, 04-api.md#roles, ADR-021/022).

Со Спринта A эндпоинты гейтятся матрицей `roles:*`; backend реализует security-инвариант
эскалации (ADR-022 §4): subset-инвариант прав и защита встроенной роли `admin`. Актор
(его права + признак привилегированности) передаётся роутером после прохождения гейта.
`user_count` — агрегат числа носителей роли.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.identity import IdentityNameError, validate_identity_name
from app.domain.permissions import (
    PermissionsValidationError,
    permissions_subset,
    validate_permissions,
)
from app.errors import forbidden, role_in_use, role_name_taken, role_not_found, unprocessable
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

# Зарезервированное имя встроенной роли (защита от правки/удаления не-админом).
_ADMIN_ROLE_NAME = "admin"


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
    """CRUD реестра ролей: валидация имени/прав, security-инвариант эскалации (ADR-022)."""

    def __init__(self, repository: RoleRepository) -> None:
        self._repo = repository

    async def list_roles(self) -> RoleListResponse:
        """Список ролей (created_at ASC, id) с `user_count`."""
        rows = await self._repo.list_all_with_counts()
        return RoleListResponse(items=[self._to_item(role, count) for role, count in rows])

    async def create_role(
        self,
        payload: RoleCreateRequest,
        *,
        actor_permissions: dict[str, list[str]],
        actor_privileged: bool,
    ) -> RoleListItem:
        """Создаёт роль. Прецеденция: валидация name/permissions (422) →
        subset-инвариант эскалации (403 для не-админа) → уникальность name (409)."""
        name = _validate_name(payload.name)
        _validate_permissions(payload.permissions)

        if not actor_privileged and not permissions_subset(payload.permissions, actor_permissions):
            logger.info("role_create_escalation_denied")
            raise forbidden()

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
        return self._to_item(role, 0)

    async def update_role(
        self,
        role_id: uuid.UUID,
        payload: RoleUpdateRequest,
        *,
        actor_permissions: dict[str, list[str]],
        actor_privileged: bool,
    ) -> RoleListItem:
        """Редактирует роль. Прецеденция: 404 → валидация name/permissions (422) →
        защита `admin` + subset-инвариант (403 для не-админа) → уникальность name (409)."""
        role = await self._repo.get_by_id(role_id)
        if role is None:
            raise role_not_found()

        fields_set = payload.model_fields_set

        new_name: str | None = None
        if "name" in fields_set and payload.name is not None:
            new_name = _validate_name(payload.name)

        if "permissions" in fields_set and payload.permissions is not None:
            _validate_permissions(payload.permissions)

        # Защита встроенной роли `admin`: менять может только привилегированный актор.
        if role.name == _ADMIN_ROLE_NAME and not actor_privileged:
            logger.info("role_update_admin_denied", role_id=str(role_id))
            raise forbidden()

        # Subset-инвариант эскалации для не-привилегированного актора.
        if (
            not actor_privileged
            and payload.permissions is not None
            and not permissions_subset(payload.permissions, actor_permissions)
        ):
            logger.info("role_update_escalation_denied", role_id=str(role_id))
            raise forbidden()

        if new_name is not None:
            if new_name != role.name and await self._repo.exists_by_name(
                new_name, exclude_id=role.id
            ):
                raise role_name_taken()
            role.name = new_name

        if "permissions" in fields_set and payload.permissions is not None:
            role.permissions = payload.permissions

        try:
            await self._repo.session.commit()
        except IntegrityError as exc:
            await self._repo.session.rollback()
            logger.info("role_update_conflict", role_id=str(role_id))
            raise role_name_taken() from exc
        await self._repo.session.refresh(role)

        count = await self._repo.count_users(role_id)
        logger.info("role_updated", role_id=str(role_id))
        return self._to_item(role, count)

    async def delete_role(self, role_id: uuid.UUID, *, actor_privileged: bool) -> None:
        """Hard-delete. Прецеденция: 404 → защита `admin` (403 для не-админа) →
        роль с носителями (409 role_in_use)."""
        role = await self._repo.get_by_id(role_id)
        if role is None:
            raise role_not_found()
        if role.name == _ADMIN_ROLE_NAME and not actor_privileged:
            logger.info("role_delete_admin_denied", role_id=str(role_id))
            raise forbidden()
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
    def _to_item(role: Role, user_count: int) -> RoleListItem:
        """Собирает элемент ответа (права уже валидированы против каталога)."""
        return RoleListItem(
            id=role.id,
            name=role.name,
            permissions=dict(role.permissions),
            user_count=user_count,
            created_at=role.created_at,
            updated_at=role.updated_at,
        )
