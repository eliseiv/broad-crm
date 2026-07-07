"""Репозиторий реестра ролей (SQLAlchemy 2.0 async, modules/auth, ADR-021)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User


class RoleRepository:
    """CRUD над таблицей `roles` + проверки уникальности имени и «роль занята»."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(self, *, name: str, permissions: dict[str, list[str]]) -> Role:
        """Создаёт роль с валидированными правами."""
        role = Role(name=name, permissions=permissions)
        self._session.add(role)
        await self._session.flush()
        await self._session.refresh(role)
        return role

    async def list_all(self) -> list[Role]:
        """Все роли, сортировка `created_at ASC, id` (детерминизм, admin первой)."""
        stmt = select(Role).order_by(Role.created_at.asc(), Role.id.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, role_id: uuid.UUID) -> Role | None:
        """Возвращает роль по id или None."""
        return await self._session.get(Role, role_id)

    async def exists_by_name(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        """Занято ли имя роли (для 409 role_name_taken).

        `exclude_id` исключает саму редактируемую роль (PATCH): смена имени на
        занятое ДРУГОЙ ролью → конфликт, сохранение своего же — нет.
        """
        stmt = select(Role.id).where(Role.name == name)
        if exclude_id is not None:
            stmt = stmt.where(Role.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.first() is not None

    async def is_in_use(self, role_id: uuid.UUID) -> bool:
        """Назначена ли роль хотя бы одному пользователю (для 409 role_in_use)."""
        stmt = select(User.id).where(User.role_id == role_id).limit(1)
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def delete_by_id(self, role_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(Role).where(Role.id == role_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
