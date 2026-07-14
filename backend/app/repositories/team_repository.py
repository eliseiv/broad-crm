"""Репозиторий реестра CRM-команд (SQLAlchemy 2.0 async, modules/teams, ADR-022/026).

`teams` + M2M `user_teams`. Лидер (`Team.leader`, опционален) и участники
(`Team.members`, порядок по `user_teams.created_at`) грузятся `selectinload`. Членство
пишется явными statements — единственная точка записи под контролем сервиса (инвариант
«если лидер задан — он ∈ участники»; авто-назначение/передача лидерства, ADR-026).
При замене состава `created_at` существующих участников СОХРАНЯЕТСЯ (дата добавления —
база порядка авто-передачи); новым ставится строго возрастающий `created_at` в порядке
входного списка (детерминизм «первого/следующего по дате»).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, insert, select, update
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

    async def list_refs(self) -> list[Team]:
        """Все команды БЕЗ агрегатов (лидер/участники не грузятся) — источник `TeamRef[]`.

        Нужен `GET /api/auth/me` при admin-уровне канала (ADR-055 §5.1: ему отдаются ВСЕ
        команды системы). Отдельный лёгкий метод, чтобы не тянуть `selectinload`
        лидера/участников ради пары `{id, name}`. Сортировка — на вызывающей стороне.
        """
        return list((await self._session.execute(select(Team))).scalars().all())

    async def get_with_members(self, team_id: uuid.UUID) -> Team | None:
        """Команда с лидером и участниками (для тела ответа / prefill) или None.

        `populate_existing=True` — ПРИНУДИТЕЛЬНО перечитывает из БД уже загруженные
        атрибуты/коллекции того же instance из identity-map. Без него повторная выборка
        после мутации состава (`replace_members` пишет `user_teams` Core-statements'ами в
        обход ORM-relationship) вернула бы тот же объект команды со СТАРОЙ коллекцией
        `members`/`leader`, и ответ PATCH отражал бы состав ДО замены (leader_id верен —
        это column-атрибут, но members/member_count/leader_username — устаревшие).
        """
        stmt = (
            select(Team)
            .options(selectinload(Team.leader), selectinload(Team.members))
            .where(Team.id == team_id)
            .execution_options(populate_existing=True)
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

    async def exists_by_mail_group_id(
        self, mail_group_id: int, *, exclude_id: uuid.UUID | None = None
    ) -> bool:
        """Занята ли группа mail-агрегатора другой командой (409 team_mail_group_taken)."""
        stmt = select(Team.id).where(Team.mail_group_id == mail_group_id)
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
        """id команд, где пользователь — лидер (для авто-передачи лидерства, ADR-026)."""
        stmt = select(Team.id).where(Team.leader_id == user_id)
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def create(
        self,
        *,
        name: str,
        leader_id: uuid.UUID | None,
        ordered_member_ids: list[uuid.UUID],
        mail_group_id: int | None = None,
    ) -> Team:
        """Создаёт команду и записывает участников (в порядке списка) в одной транзакции.

        `leader_id` может быть None (команда без лидера). Инвариант «лидер ∈ участники»
        обеспечивает вызывающий сервис (лидер уже включён в `ordered_member_ids`).
        `mail_group_id` — привязка к группе mail-агрегатора (ADR-038); None — без привязки.
        """
        team = Team(name=name, leader_id=leader_id, mail_group_id=mail_group_id)
        self._session.add(team)
        await self._session.flush()
        await self._insert_members(team.id, ordered_member_ids, base=datetime.now(UTC))
        return team

    async def replace_members(
        self, team_id: uuid.UUID, ordered_member_ids: list[uuid.UUID]
    ) -> None:
        """Приводит состав команды к `ordered_member_ids`, сохраняя `created_at` остающихся.

        Выбывшие участники удаляются; новые добавляются со строго возрастающим
        `created_at` ПОСЛЕ максимального существующего (дата добавления → порядок
        авто-передачи, ADR-026). Порядок новых — по позиции во входном списке.
        """
        existing = await self._member_created_at(team_id)
        desired = list(dict.fromkeys(ordered_member_ids))
        desired_set = set(desired)

        to_remove = set(existing) - desired_set
        if to_remove:
            await self._session.execute(
                delete(user_teams).where(
                    user_teams.c.team_id == team_id,
                    user_teams.c.user_id.in_(to_remove),
                )
            )

        new_members = [uid for uid in desired if uid not in existing]
        if new_members:
            base = max(existing.values()) if existing else datetime.now(UTC)
            await self._insert_members(team_id, new_members, base=base, offset=1)

    async def get_first_member(self, team_id: uuid.UUID) -> uuid.UUID | None:
        """Первый участник по `(created_at ASC, user_id ASC)` (кандидат в лидеры) или None."""
        stmt = (
            select(user_teams.c.user_id)
            .where(user_teams.c.team_id == team_id)
            .order_by(user_teams.c.created_at.asc(), user_teams.c.user_id.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def promote_next_leader(
        self, team_id: uuid.UUID, *, exclude_user_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Назначает лидером следующего участника (по дате), исключая `exclude_user_id`.

        Если других участников нет → `leader_id = NULL` (команда без лидера, ADR-026).
        Возвращает нового лидера (или None). Вызывается перед исключением/удалением
        текущего лидера (его строка `user_teams` ещё может присутствовать — потому
        `exclude_user_id`).
        """
        stmt = (
            select(user_teams.c.user_id)
            .where(
                user_teams.c.team_id == team_id,
                user_teams.c.user_id != exclude_user_id,
            )
            .order_by(user_teams.c.created_at.asc(), user_teams.c.user_id.asc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        next_leader = result.scalars().first()
        await self._session.execute(
            update(Team).where(Team.id == team_id).values(leader_id=next_leader)
        )
        return next_leader

    async def delete_by_id(self, team_id: uuid.UUID) -> bool:
        """Hard-delete по id (каскад `user_teams`). True, если запись была удалена."""
        stmt = delete(Team).where(Team.id == team_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def _member_created_at(self, team_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
        """Текущие участники команды с их `created_at` (для сохранения дат при замене)."""
        stmt = select(user_teams.c.user_id, user_teams.c.created_at).where(
            user_teams.c.team_id == team_id
        )
        result = await self._session.execute(stmt)
        return {row.user_id: row.created_at for row in result}

    async def _insert_members(
        self,
        team_id: uuid.UUID,
        member_ids: list[uuid.UUID],
        *,
        base: datetime,
        offset: int = 0,
    ) -> None:
        """Вставляет строки `user_teams` со строго возрастающим `created_at` в порядке списка.

        `created_at = base + (offset + i) микросекунд` — сохраняет порядок входного
        списка и (при `offset=1`) размещает новых участников после максимального
        существующего `created_at`.
        """
        if not member_ids:
            return
        rows = [
            {
                "user_id": uid,
                "team_id": team_id,
                "created_at": base + timedelta(microseconds=offset + index),
            }
            for index, uid in enumerate(member_ids)
        ]
        await self._session.execute(insert(user_teams), rows)
