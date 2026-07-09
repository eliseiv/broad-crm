"""Репозиторий доставок SMS в Telegram (SQLAlchemy 2.0 async, modules/sms, ADR-030).

Порт донорского `DeliveryRepository`. `try_reserve` идемпотентен по UNIQUE
`(inbound_sms_id, telegram_user_id)` (`ON CONFLICT DO NOTHING`) — база
crash-recoverable fan-out. `pending()` добирает кандидатов retry-монитора
(partial-индекс `ix_sms_deliveries_retry`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sms_delivery import SmsDelivery

_LAST_ERROR_MAX = 1000


class SmsDeliveryRepository:
    """Резервирование/статусы доставок + выборка кандидатов на retry."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_reserve(
        self, *, inbound_sms_id: int, user_id: uuid.UUID, telegram_user_id: int
    ) -> int | None:
        """Зарезервировать доставку в чат. `None` → уже была (идемпотентность fan-out)."""
        stmt = (
            pg_insert(SmsDelivery)
            .values(
                inbound_sms_id=inbound_sms_id,
                user_id=user_id,
                telegram_user_id=telegram_user_id,
                status="pending",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    SmsDelivery.inbound_sms_id,
                    SmsDelivery.telegram_user_id,
                ]
            )
            .returning(SmsDelivery.id)
        )
        row = (await self._session.execute(stmt)).first()
        return int(row[0]) if row is not None else None

    async def mark_sent(self, delivery_id: int) -> None:
        """Успешная доставка: `sent`, `sent_at`, инкремент попыток, сброс ошибки."""
        now = datetime.now(UTC)
        await self._session.execute(
            update(SmsDelivery)
            .where(SmsDelivery.id == delivery_id)
            .values(
                status="sent",
                sent_at=now,
                attempts=SmsDelivery.attempts + 1,
                last_error=None,
                updated_at=now,
            )
        )

    async def mark_failed(self, delivery_id: int, error_message: str) -> None:
        """Ретраибельный сбой Bot API: `failed` (переотправит retry-монитор)."""
        await self._session.execute(
            update(SmsDelivery)
            .where(SmsDelivery.id == delivery_id)
            .values(
                status="failed",
                attempts=SmsDelivery.attempts + 1,
                last_error=error_message[:_LAST_ERROR_MAX],
                updated_at=datetime.now(UTC),
            )
        )

    async def mark_dead(self, delivery_id: int, error_message: str) -> None:
        """`403`/forbidden от Bot API: `dead` (линк мёртв, оператор перепривязывает)."""
        await self._session.execute(
            update(SmsDelivery)
            .where(SmsDelivery.id == delivery_id)
            .values(
                status="dead",
                attempts=SmsDelivery.attempts + 1,
                last_error=error_message[:_LAST_ERROR_MAX],
                updated_at=datetime.now(UTC),
            )
        )

    async def pending(self, max_attempts: int, limit: int = 100) -> list[SmsDelivery]:
        """Кандидаты на повтор: `status ∈ (pending, failed)` и `attempts < max_attempts`."""
        stmt = (
            select(SmsDelivery)
            .where(
                SmsDelivery.status.in_(("pending", "failed")),
                SmsDelivery.attempts < max_attempts,
            )
            .order_by(SmsDelivery.id)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())
