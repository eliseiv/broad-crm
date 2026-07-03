"""Репозиторий персистентного состояния нотификатора (SQLAlchemy 2.0 async).

Две операции (modules/notifier «Персистентность состояния»): загрузка состояний
для набора `server_id` (`dict[server_id -> row]`) и UPSERT состояния сервера
(`INSERT ... ON CONFLICT (server_id) DO UPDATE`). БД — источник истины состояния
нотификатора (ADR-014); коммит выполняет вызывающий сервис.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifier_server_state import NotifierServerState


class NotifierServerStateRepository:
    """Загрузка и UPSERT строк `notifier_server_state`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (управление транзакцией — в сервисе)."""
        return self._session

    async def load_states(
        self, server_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, NotifierServerState]:
        """Состояния для указанных серверов: `dict[server_id -> row]`.

        Пустой набор → пустой словарь (без запроса). Отсутствующая строка
        трактуется вызывающим кодом как `prev is None` (здоровый baseline, ADR-014).
        """
        if not server_ids:
            return {}
        stmt = select(NotifierServerState).where(NotifierServerState.server_id.in_(server_ids))
        result = await self._session.execute(stmt)
        return {row.server_id: row for row in result.scalars().all()}

    async def upsert(
        self,
        server_id: uuid.UUID,
        *,
        online: bool,
        zone_cpu: str | None,
        zone_ram: str | None,
        zone_ssd: str | None,
    ) -> None:
        """UPSERT состояния сервера (`ON CONFLICT (server_id) DO UPDATE`).

        `updated_at` всегда пишется `now()`. Коммит — на вызывающем сервисе.
        """
        stmt = pg_insert(NotifierServerState).values(
            server_id=server_id,
            online=online,
            zone_cpu=zone_cpu,
            zone_ram=zone_ram,
            zone_ssd=zone_ssd,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[NotifierServerState.server_id],
            set_={
                "online": online,
                "zone_cpu": zone_cpu,
                "zone_ram": zone_ram,
                "zone_ssd": zone_ssd,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(stmt)
