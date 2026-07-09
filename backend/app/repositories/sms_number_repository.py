"""Репозиторий реестра SMS-номеров (SQLAlchemy 2.0 async, modules/sms, ADR-030).

Порт донорского `PhoneNumberRepository`. `team` грузится `selectinload` для сборки
`SmsTeamRef`. Сортировка списков — `created_at DESC, id DESC` (04-api.md). Upsert
`sync` идемпотентен (`ON CONFLICT (phone_number) DO NOTHING`); `label` обновляется
отдельным bulk-UPDATE из Twilio `friendly_name`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.sms_phone_number import SmsPhoneNumber


class SmsNumberRepository:
    """CRUD над `sms_phone_numbers` + upsert-sync + выборки по scope."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (управление транзакцией в сервисе)."""
        return self._session

    async def get_by_id(self, number_id: int) -> SmsPhoneNumber | None:
        """Номер по id (с загруженной командой) или None.

        `populate_existing=True` ПРИНУДИТЕЛЬНО перечитывает атрибуты/relationship
        уже загруженного instance из identity-map (`expire_on_commit=False`). Без него
        reload после Core-UPDATE в обход ORM (`set_team` при transfer, правка полей)
        вернул бы тот же объект со СТАЛОЙ командой/полями → 200 с устаревшим телом
        (тот же класс бага, что уже чинили в `TeamRepository.get_with_members`).
        """
        stmt = (
            select(SmsPhoneNumber)
            .options(selectinload(SmsPhoneNumber.team))
            .where(SmsPhoneNumber.id == number_id)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_by_phone(self, phone_number: str) -> SmsPhoneNumber | None:
        """Номер по E.164 (для резолва команды приёма) или None."""
        stmt = select(SmsPhoneNumber).where(SmsPhoneNumber.phone_number == phone_number)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def find_many_by_phones(self, phones: Iterable[str]) -> list[SmsPhoneNumber]:
        """Номера (с командой) по набору E.164 — для резолва текущего номера ленты."""
        phone_list = list(phones)
        if not phone_list:
            return []
        stmt = (
            select(SmsPhoneNumber)
            .options(selectinload(SmsPhoneNumber.team))
            .where(SmsPhoneNumber.phone_number.in_(phone_list))
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_all(self) -> list[SmsPhoneNumber]:
        """Все номера (с командой), сортировка `created_at DESC, id DESC` (супер-админ)."""
        stmt = (
            select(SmsPhoneNumber)
            .options(selectinload(SmsPhoneNumber.team))
            .order_by(SmsPhoneNumber.created_at.desc(), SmsPhoneNumber.id.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_by_team(self, team_id: uuid.UUID) -> list[SmsPhoneNumber]:
        """Номера конкретной команды (с командой), `created_at DESC, id DESC`."""
        stmt = (
            select(SmsPhoneNumber)
            .options(selectinload(SmsPhoneNumber.team))
            .where(SmsPhoneNumber.team_id == team_id)
            .order_by(SmsPhoneNumber.created_at.desc(), SmsPhoneNumber.id.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_by_teams(self, team_ids: Iterable[uuid.UUID]) -> list[SmsPhoneNumber]:
        """Номера набора команд (scope не-админа), `created_at DESC, id DESC`."""
        ids = list(team_ids)
        if not ids:
            return []
        stmt = (
            select(SmsPhoneNumber)
            .options(selectinload(SmsPhoneNumber.team))
            .where(SmsPhoneNumber.team_id.in_(ids))
            .order_by(SmsPhoneNumber.created_at.desc(), SmsPhoneNumber.id.desc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_by_teams(self, team_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
        """`{team_id: число номеров}` для набора команд (агрегат `number_count`)."""
        ids = list(team_ids)
        if not ids:
            return {}
        stmt = (
            select(SmsPhoneNumber.team_id, func.count())
            .where(SmsPhoneNumber.team_id.in_(ids))
            .group_by(SmsPhoneNumber.team_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {tid: int(cnt) for tid, cnt in rows if tid is not None}

    async def count_by_team(self, team_id: uuid.UUID) -> int:
        """Число номеров одной команды (агрегат `number_count`)."""
        stmt = (
            select(func.count())
            .select_from(SmsPhoneNumber)
            .where(SmsPhoneNumber.team_id == team_id)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def set_team(self, number_id: int, team_id: uuid.UUID | None) -> None:
        """Назначить/снять команду у номера (transfer). `team_id=None` → unassigned."""
        await self._session.execute(
            update(SmsPhoneNumber)
            .where(SmsPhoneNumber.id == number_id)
            .values(team_id=team_id, updated_at=datetime.now(UTC))
        )

    async def delete_by_id(self, number_id: int) -> bool:
        """Hard-delete номера. True, если строка была удалена (история SMS сохраняется)."""
        result = await self._session.execute(
            delete(SmsPhoneNumber).where(SmsPhoneNumber.id == number_id)
        )
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def bulk_upsert_unassigned(self, numbers: list[str]) -> int:
        """Идемпотентный батч-upsert номеров как unassigned (`ON CONFLICT DO NOTHING`).

        `numbers` — уже нормализованные (E.164) и дедуплицированные. Вставка
        `team_id=NULL`, `added_by_user_id=NULL`, `label=NULL`; существующие (в т.ч.
        назначенные командам) НЕ трогаются. Возвращает число реально вставленных строк.
        """
        if not numbers:
            return 0
        rows = [
            {
                "phone_number": phone,
                "team_id": None,
                "added_by_user_id": None,
                "label": None,
            }
            for phone in numbers
        ]
        stmt = (
            pg_insert(SmsPhoneNumber)
            .values(rows)
            .on_conflict_do_nothing(index_elements=[SmsPhoneNumber.phone_number])
            .returning(SmsPhoneNumber.id)
        )
        result = await self._session.execute(stmt)
        return len(result.fetchall())

    async def update_label(self, phone_number: str, label: str | None) -> None:
        """Обновить системный `label` номера из Twilio `friendly_name` (только `sync`)."""
        await self._session.execute(
            update(SmsPhoneNumber)
            .where(SmsPhoneNumber.phone_number == phone_number)
            .values(label=label, updated_at=datetime.now(UTC))
        )
