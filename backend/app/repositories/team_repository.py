"""Репозиторий реестра CRM-команд (SQLAlchemy 2.0 async, modules/teams, ADR-022).

`teams` + M2M `user_teams`. Лидер (`Team.leader`) и участники (`Team.members`)
грузятся `selectinload`. Членство пишется явными statements (`_write_members`) —
единственная точка записи под контролем сервиса (инвариант «лидер ∈ участники»).
Существование пользователей (`leader_id`/`member_ids`) валидирует сервис через
`UserRepository`; здесь — существование команд и «команды, ведомые пользователем».
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.team import Team, user_teams


class TeamRepository:
    """CRUD над `teams` + членство `user_teams` + агрегаты (лидер/участники)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def list_all(self) -> list[Team]:
        """Все команды (лидер + участники), сортировка `created_at DESC, id`."""
        stmt = (
            select(Team)
            .options(selectinload(Team.leader), selectinload(Team.members))
            .order_by(Team.created_at.desc(), Team.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.unique().scalars().all())

    async def get_with_members(self, team_id: uuid.UUID) -> Team | None:
        """Команда с лидером и участниками (для тела ответа / prefill) или None."""
        stmt = (
            select(Team)
            .options(selectinload(Team.leader), selectinload(Team.members))
            .where(Team.id == team_id)
        )
        result = await self._session.execute(stmt)
        return result.unique().scalar_one_or_none()

    async def get(self, team_id: uuid.UUID) -> Team | None:
        """Возвращает команду по id или None (для мутации, без агрегатов)."""
        return await self._session.get(Team, team_id)

    async def exists_by_name(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        """Занято ли имя команды (для 409 team_name_taken)."""
        stmt = select(Team.id).where(Team.name == name)
        if exclude_id is not None:
            stmt = stmt.where(Team.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.first() is not None

    async def get_existing_ids(self, ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """Подмножество `ids`, реально существующее в `teams`."""
        if not ids:
            return set()
        stmt = select(Team.id).where(Team.id.in_(ids))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def ids_led_by(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """id команд, где пользователь — лидер (для инварианта «лидер ∈ участники»)."""
        stmt = select(Team.id).where(Team.leader_id == user_id)
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def create(self, *, name: str, leader_id: uuid.UUID, member_ids: set[uuid.UUID]) -> Team:
        """Создаёт команду и записывает членство (лидер включён) в одной транзакции."""
        team = Team(name=name, leader_id=leader_id)
        self._session.add(team)
        await self._session.flush()
        await self._write_members(team.id, member_ids | {leader_id})
        return team

    async def replace_members(self, team_id: uuid.UUID, member_ids: set[uuid.UUID]) -> None:
        """Полностью заменяет состав команды (лидер уже включён вызывающим сервисом)."""
        await self._write_members(team_id, member_ids)

    async def delete_by_id(self, team_id: uuid.UUID) -> bool:
        """Hard-delete по id (каскад `user_teams`). True, если запись была удалена."""
        stmt = delete(Team).where(Team.id == team_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def _write_members(self, team_id: uuid.UUID, member_ids: set[uuid.UUID]) -> None:
        """Удаляет прежние строки `user_teams` команды и вставляет новый набор."""
        await self._session.execute(delete(user_teams).where(user_teams.c.team_id == team_id))
        if member_ids:
            await self._session.execute(
                insert(user_teams),
                [{"user_id": uid, "team_id": team_id} for uid in member_ids],
            )
