"""Репозиторий реестра пользователей (SQLAlchemy 2.0 async, modules/auth, ADR-021).

Роль подгружается eager через `User.role` (`lazy="joined"`) — `get_by_id`/
`get_by_username`/`list_all` возвращают пользователя с доступным `.role` без
ленивого IO (безопасно в async).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """CRUD над таблицей `users` + проверка уникальности username."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(self, *, username: str, password_hash: str, role_id: uuid.UUID) -> User:
        """Создаёт пользователя (пароль — только bcrypt-хэш)."""
        user = User(username=username, password_hash=password_hash, role_id=role_id)
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user

    async def list_all(self) -> list[User]:
        """Все пользователи (с ролью), сортировка `created_at ASC, id`."""
        stmt = select(User).order_by(User.created_at.asc(), User.id.asc())
        result = await self._session.execute(stmt)
        return list(result.unique().scalars().all())

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Возвращает пользователя (с ролью) по id или None."""
        return await self._session.get(User, user_id)

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

    async def delete_by_id(self, user_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(User).where(User.id == user_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
