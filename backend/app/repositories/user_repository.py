"""Репозиторий реестра пользователей (SQLAlchemy 2.0 async, modules/auth, ADR-021/022).

Роль подгружается eager через `User.role` (`lazy="joined"`). CRM-команды (`User.teams`)
грузятся точечно через `selectinload` в `list_all`/`get_with_teams` (в hot-path
принципала `get_by_id` не загружаются). Членство в командах (`user_teams`) пишется
явными statements (`set_membership`) — источник записи под контролем сервиса.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.team import user_teams
from app.models.user import User


class UserRepository:
    """CRUD над таблицей `users` + уникальность username/email + членство в командах."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(
        self,
        *,
        username: str,
        email: str | None,
        password_hash: str,
        role_id: uuid.UUID,
    ) -> User:
        """Создаёт пользователя (пароль — только bcrypt-хэш). Членство — отдельно."""
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            role_id=role_id,
        )
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def list_all(self) -> list[User]:
        """Все пользователи (с ролью и командами), сортировка `created_at ASC, id`."""
        stmt = (
            select(User)
            .options(selectinload(User.teams))
            .order_by(User.created_at.asc(), User.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.unique().scalars().all())

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Возвращает пользователя (с ролью) по id или None. Без загрузки команд."""
        return await self._session.get(User, user_id)

    async def get_with_teams(self, user_id: uuid.UUID) -> User | None:
        """Пользователь с ролью и командами (для тела ответа users API) или None."""
        stmt = select(User).options(selectinload(User.teams)).where(User.id == user_id)
        result = await self._session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        """Возвращает пользователя (с ролью) по username или None (для логина)."""
        stmt = select(User).where(User.username == username).limit(1)
        result = await self._session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def exists_by_username(
        self, username: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        """Занят ли username (для 409 username_taken)."""
        stmt = select(User.id).where(User.username == username)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.first() is not None

    async def exists_by_email(self, email: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        """Занят ли email среди заданных (для 409 email_taken). `email` — нормализован."""
        stmt = select(User.id).where(User.email == email)
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.first() is not None

    async def get_existing_ids(self, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """Подмножество `ids`, реально существующее в `users` (валидация ссылок)."""
        if not ids:
            return set()
        stmt = select(User.id).where(User.id.in_(ids))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def set_membership(self, user_id: uuid.UUID, team_ids: set[uuid.UUID]) -> None:
        """Полностью заменяет членство пользователя в командах (в текущей транзакции)."""
        await self._session.execute(delete(user_teams).where(user_teams.c.user_id == user_id))
        if team_ids:
            await self._session.execute(
                insert(user_teams),
                [{"user_id": user_id, "team_id": tid} for tid in team_ids],
            )

    async def delete_by_id(self, user_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(User).where(User.id == user_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
