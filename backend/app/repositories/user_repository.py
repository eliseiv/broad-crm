"""Репозиторий реестра пользователей (SQLAlchemy 2.0 async, modules/auth, ADR-021/025).

Роль подгружается eager через `User.role` (`lazy="joined"`). CRM-команды (`User.teams`)
грузятся точечно через `selectinload` в `list_all`/`get_with_teams` (в hot-path
принципала `get_by_id` не загружаются). Членство в командах (`user_teams`) пишется
явными statements (`set_membership`) — источник записи под контролем сервиса; при
изменении набора команд `created_at` существующих строк СОХРАНЯЕТСЯ (дата добавления
важна для авто-передачи лидерства, ADR-026), новым строкам ставится `now()`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.team import user_teams
from app.models.user import User


class UserRepository:
    """CRUD над `users` + уникальность username/telegram + членство в командах."""

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
        telegram: str | None,
        password_hash: str | None,
        role_id: uuid.UUID,
    ) -> User:
        """Создаёт пользователя (пароль — только bcrypt-хэш ИЛИ None для беспарольного).

        Членство в командах записывается отдельно (`set_membership`).
        """
        user = User(
            username=username,
            telegram=telegram,
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

    async def get_by_telegram(self, telegram: str) -> User | None:
        """Возвращает пользователя (с ролью) по нормализованному telegram или None.

        Используется вторым шагом резолвинга логина (вход по Телеграму, ADR-025).
        Пустой идентификатор не матчит (telegram хранится непустым нормализованным).
        """
        if not telegram:
            return None
        stmt = select(User).where(User.telegram == telegram).limit(1)
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

    async def exists_by_telegram(
        self, telegram: str, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        """Занят ли telegram среди заданных (для 409 telegram_taken). Уже нормализован."""
        stmt = select(User.id).where(User.telegram == telegram)
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

    async def team_ids_of_user(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """Текущий набор id команд пользователя (для вычисления выбывших при PATCH)."""
        stmt = select(user_teams.c.team_id).where(user_teams.c.user_id == user_id)
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def set_membership(self, user_id: uuid.UUID, team_ids: set[uuid.UUID]) -> None:
        """Приводит членство пользователя к набору `team_ids` (в текущей транзакции).

        Существующие строки СОХРАНЯЮТ `created_at` (дата добавления — база авто-передачи
        лидерства, ADR-026): удаляются только выбывшие, добавляются только новые (с
        `created_at = DEFAULT now()`).
        """
        current = await self.team_ids_of_user(user_id)
        to_remove = current - team_ids
        to_add = team_ids - current
        if to_remove:
            await self._session.execute(
                delete(user_teams).where(
                    user_teams.c.user_id == user_id,
                    user_teams.c.team_id.in_(to_remove),
                )
            )
        if to_add:
            await self._session.execute(
                insert(user_teams),
                [{"user_id": user_id, "team_id": tid} for tid in to_add],
            )

    async def delete_by_id(self, user_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(User).where(User.id == user_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]
