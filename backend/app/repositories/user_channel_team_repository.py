"""Репозиторий per-channel добавок команд `user_channel_teams` (ADR-055 §2/§3/§5).

Единая точка чтения/записи добавок канала. Хранится **только добавка** — команды из
`user_teams` того же пользователя сюда не пишутся (инвариант нормализации §2.3
обеспечивают сервисы users/teams; здесь — примитивы, которыми они это делают:
`replace_extras` (путь 1, users CRUD) и `remove_team_for_users` (путь 2, teams CRUD)).

Эффективный scope канала (`scope_team_ids`) собирается **одним UNION-запросом**
`user_teams ∪ user_channel_teams[channel]` — не двумя round-trip'ами (ADR-055 §3).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import CompoundSelect, delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.channels import Channel
from app.models.team import Team, user_teams
from app.models.user_channel_team import user_channel_teams


class UserChannelTeamRepository:
    """Добавки команд по каналам: scope, эффективные ссылки, запись, нормализация."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (управление транзакцией — в сервисе)."""
        return self._session

    async def scope_team_ids(self, user_id: uuid.UUID, channel: Channel) -> frozenset[uuid.UUID]:
        """Эффективные команды канала: `user_teams ∪ user_channel_teams[channel]`.

        Одним запросом-`UNION` (ADR-055 §3) — источник `MailScope.team_ids` /
        `SmsScope.team_ids`. Пустой набор — легитимен (пользователь без команд).
        """
        stmt = self._effective_ids_stmt(user_id, channel)
        result = await self._session.execute(stmt)
        return frozenset(result.scalars().all())

    async def effective_teams(self, user_id: uuid.UUID, channel: Channel) -> list[Team]:
        """Команды канала (id + name) для `GET /api/auth/me` (ADR-055 §5.1).

        ЭФФЕКТИВНЫЙ scope (базовые ∪ добавка), а не только добавка. Один запрос:
        `teams` по подзапросу-`UNION`. Сортировка — на вызывающей стороне (ru, ci).
        """
        stmt = select(Team).where(Team.id.in_(self._effective_ids_stmt(user_id, channel)))
        return list((await self._session.execute(stmt)).scalars().all())

    async def extra_team_ids(self, user_id: uuid.UUID, channel: Channel) -> set[uuid.UUID]:
        """ТОЛЬКО хранимая добавка канала (без базовых команд) — для `PATCH` без поля."""
        stmt = select(user_channel_teams.c.team_id).where(
            user_channel_teams.c.user_id == user_id,
            user_channel_teams.c.channel == channel,
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def extras_for_users(
        self, user_ids: Iterable[uuid.UUID]
    ) -> dict[tuple[uuid.UUID, str], list[Team]]:
        """Добавки набора пользователей: `{(user_id, channel): [Team, ...]}` — БЕЗ N+1.

        Один JOIN `user_channel_teams → teams` на весь список `GET /api/users`
        (`mail_extra_teams`/`sms_extra_teams`, ADR-055 §5.2). Пары без добавок в
        результат не попадают — вызывающий подставляет `[]`.
        """
        ids = list(user_ids)
        if not ids:
            return {}
        stmt = (
            select(user_channel_teams.c.user_id, user_channel_teams.c.channel, Team)
            .join(Team, Team.id == user_channel_teams.c.team_id)
            .where(user_channel_teams.c.user_id.in_(ids))
        )
        rows = (await self._session.execute(stmt)).all()
        extras: dict[tuple[uuid.UUID, str], list[Team]] = {}
        for user_id, channel, team in rows:
            extras.setdefault((user_id, channel), []).append(team)
        return extras

    async def replace_extras(
        self, user_id: uuid.UUID, channel: Channel, team_ids: set[uuid.UUID]
    ) -> None:
        """Приводит добавку канала к набору `team_ids` (путь 1 инварианта §2.3).

        Вызывающий сервис ОБЯЗАН передать набор, из которого уже **вычтено** базовое
        членство (`extra := <channel>_extra_team_ids − team_ids`): в таблице добавок
        базовые команды не хранятся. Пустой набор → добавка снимается целиком.
        """
        await self._session.execute(
            delete(user_channel_teams).where(
                user_channel_teams.c.user_id == user_id,
                user_channel_teams.c.channel == channel,
            )
        )
        if team_ids:
            await self._session.execute(
                insert(user_channel_teams),
                [
                    {"user_id": user_id, "channel": channel, "team_id": team_id}
                    for team_id in team_ids
                ],
            )

    async def remove_team_for_users(
        self, team_id: uuid.UUID, user_ids: Iterable[uuid.UUID]
    ) -> None:
        """Снимает команду `team_id` с добавок ОБОИХ каналов у `user_ids` (путь 2 §2.3).

        Вызывается teams CRUD (`POST`/`PATCH /api/teams`) в ТОЙ ЖЕ транзакции, что и
        приведение состава: ставший участником команды не хранит её же как добавку.
        Без этого исключение участника из команды на `/teams` оставляло бы «висящий»
        доступ к почте/СМС этой команды (ADR-055 §2.3, разбор пути 2).
        """
        ids = list(user_ids)
        if not ids:
            return
        await self._session.execute(
            delete(user_channel_teams).where(
                user_channel_teams.c.team_id == team_id,
                user_channel_teams.c.user_id.in_(ids),
            )
        )

    @staticmethod
    def _effective_ids_stmt(
        user_id: uuid.UUID, channel: Channel
    ) -> CompoundSelect[tuple[uuid.UUID]]:
        """`SELECT team_id FROM user_teams … UNION SELECT team_id FROM user_channel_teams …`."""
        base = select(user_teams.c.team_id).where(user_teams.c.user_id == user_id)
        extra = select(user_channel_teams.c.team_id).where(
            user_channel_teams.c.user_id == user_id,
            user_channel_teams.c.channel == channel,
        )
        return base.union(extra)
