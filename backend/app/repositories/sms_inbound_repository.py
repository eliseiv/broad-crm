"""Репозиторий входящих SMS (SQLAlchemy 2.0 async, modules/sms, ADR-030).

Порт донорского `SmsRepository`. Дедуп по `twilio_message_sid` (partial-UNIQUE).
Keyset-листинг ленты по `(received_at DESC, id DESC)` с предикатом
`(received_at, id) < cursor`. Видимость (набор `to_number`) вычисляет сервис.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sms_inbound import SmsInbound


class SmsInboundRepository:
    """CRUD над `sms_inbound` + дедуп по SID + keyset-листинг ленты."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_sid(self, sid: str) -> SmsInbound | None:
        """SMS по `twilio_message_sid` (дедуп ретраев webhook) или None."""
        stmt = select(SmsInbound).where(SmsInbound.twilio_message_sid == sid).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get(self, sms_id: int) -> SmsInbound | None:
        """SMS по id (для retry-монитора) или None."""
        return await self._session.get(SmsInbound, sms_id)

    async def create(
        self,
        *,
        twilio_message_sid: str | None,
        from_number: str,
        to_number: str,
        body: str,
        team_id: uuid.UUID | None,
        raw_payload: dict[str, Any],
        received_at: datetime | None = None,
    ) -> SmsInbound:
        """Сохраняет входящее SMS (снимок команды приёма в `team_id`)."""
        sms = SmsInbound(
            twilio_message_sid=twilio_message_sid,
            from_number=from_number,
            to_number=to_number,
            body=body,
            team_id=team_id,
            raw_payload=raw_payload,
            received_at=received_at or datetime.now(UTC),
        )
        self._session.add(sms)
        await self._session.flush()
        await self._session.refresh(sms)
        return sms

    async def list_inbound(
        self,
        *,
        to_numbers: list[str] | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
    ) -> list[SmsInbound]:
        """Keyset-листинг ленты (04-api.md#get-apismsmessages).

        `to_numbers` — набор видимых номеров (`to_number IN (...)`): `None` — без
        фильтра (супер-админ без фильтров, все SMS); пустой список — пустой результат
        (без запроса). `cursor` — позиция `(received_at, id)` для предиката
        `(received_at, id) < (r0, id0)`. Сортировка `received_at DESC, id DESC`.
        Вызывающий передаёт `limit + 1` для определения следующей страницы.
        """
        if to_numbers is not None and len(to_numbers) == 0:
            return []
        stmt = select(SmsInbound)
        if to_numbers is not None:
            stmt = stmt.where(SmsInbound.to_number.in_(to_numbers))
        if cursor is not None:
            r0, id0 = cursor
            stmt = stmt.where(
                or_(
                    SmsInbound.received_at < r0,
                    and_(SmsInbound.received_at == r0, SmsInbound.id < id0),
                )
            )
        stmt = stmt.order_by(SmsInbound.received_at.desc(), SmsInbound.id.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())
