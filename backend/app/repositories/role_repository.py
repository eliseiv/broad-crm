"""Репозиторий реестра ролей (SQLAlchemy 2.0 async, modules/auth, ADR-021)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.role import Role
from app.models.user import User
from app.models.user_role import user_roles


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

    async def list_all_with_counts(self) -> list[tuple[Role, int]]:
        """Все роли с числом носителей (`user_count`), сортировка `created_at ASC, id`.

        Агрегат `COUNT(DISTINCT u.id)` через LEFT JOIN на `user_roles` (ADR-079 §1) —
        роли без носителей отдаются с `user_count=0` (ADR-022). `DISTINCT` обязателен:
        M2M-join даёт по строке на каждую пару, и пользователь с двумя ролями иначе
        посчитался бы дважды в каждой.

        **Отображение исключает системную строку-якорь** (`NOT is_system`, ADR-051 §1.5):
        роль `admin` не должна получать фантомного «+1 пользователь» в UI. Предикат — в
        условии JOIN (а не в `WHERE`), иначе LEFT JOIN выродился бы в INNER и роли без
        носителей пропали бы из выдачи.
        """
        stmt = (
            select(Role, func.count(func.distinct(User.id)))
            .outerjoin(user_roles, user_roles.c.role_id == Role.id)
            .outerjoin(
                User,
                (User.id == user_roles.c.user_id) & User.is_system.is_(False),
            )
            .group_by(Role.id)
            .order_by(Role.created_at.asc(), Role.id.asc())
        )
        result = await self._session.execute(stmt)
        return [(role, int(count)) for role, count in result.all()]

    async def count_users(self, role_id: uuid.UUID) -> int:
        """Число пользователей с этой ролью (для `user_count` в ответе POST/PATCH).

        Отображение — якорь исключён (`NOT is_system`, ADR-051 §1.5); счёт по
        `COUNT(DISTINCT u.id)` через `user_roles` (ADR-079 §1).
        """
        stmt = (
            select(func.count(func.distinct(User.id)))
            .select_from(user_roles)
            .join(User, User.id == user_roles.c.user_id)
            .where(user_roles.c.role_id == role_id, User.is_system.is_(False))
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    async def get_by_id(self, role_id: uuid.UUID) -> Role | None:
        """Возвращает роль по id или None."""
        return await self._session.get(Role, role_id)

    async def list_refs(self) -> list[Role]:
        """Все роли без агрегатов (`{id, name}`) — источник `role-refs` для модалки видимости.

        Лёгкий метод под `GET /api/documents/role-refs` (гейт `documents:share`, ADR-059):
        НЕ переиспользует admin-gated `GET /api/roles` (класс дефекта TD-050). Сортировка —
        на вызывающей стороне.
        """
        return list((await self._session.execute(select(Role))).scalars().all())

    async def existing_ids(self, role_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """Подмножество `role_ids`, реально существующих в `roles` (валидация `visibility`).

        Несуществующий `role_id` в `PATCH /nodes/{id}/visibility` → 422 validation_error
        (04-api.md). Пустой вход → пустой результат без запроса.
        """
        if not role_ids:
            return set()
        stmt = select(Role.id).where(Role.id.in_(role_ids))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

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
        """Назначена ли роль хотя бы одному пользователю (для 409 role_in_use).

        **Гард удаления ВКЛЮЧАЕТ системную строку-якорь** (фильтра `is_system` здесь НЕТ —
        ADR-051 §1.5): метод обязан быть зеркалом FK `user_roles.role_id → roles.id
        ON DELETE RESTRICT` (ADR-079 §1), иначе `DELETE /api/roles/{id}` вместо
        `409 role_in_use` упал бы IntegrityError → 500. Осознанное следствие: роль,
        которую держит якорь (по умолчанию встроенная `admin`), не удаляется даже при
        `user_count = 0`.
        """
        stmt = select(user_roles.c.user_id).where(user_roles.c.role_id == role_id).limit(1)
        result = await self._session.execute(stmt)
        return result.first() is not None

    async def delete_by_id(self, role_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(Role).where(Role.id == role_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
