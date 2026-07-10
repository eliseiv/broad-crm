"""Репозиторий `mail_telegram_notifications` — дедуп доставки + recovery (ADR-044 §6).

`try_reserve` — застолбить `(message_id, telegram_user_id)` до отправки (`ON CONFLICT
DO NOTHING` по `uq_mail_tg_notif_msg_chat`, идемпотентность «ровно один на переход»).
`mark_sent`/`mark_failed`/`mark_dead` — финализация. `pending_for_retry` — кандидаты
прохода B (`status IN (pending,failed) AND attempts < :max`), восстанавливает
`tg_notify_recovery` агрегатора: транзиентный сбой не теряет уведомление навсегда.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_telegram import MailTelegramNotification


@dataclass(frozen=True, slots=True)
class PendingNotification:
    """Строка доставки-кандидата на повтор (проход B)."""

    id: uuid.UUID
    message_id: int
    telegram_user_id: int
    attempts: int


class MailNotificationRepository:
    """Резервирование доставок + переходы статусов + выборка на recovery."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_reserve(self, *, message_id: int, telegram_user_id: int) -> uuid.UUID | None:
        """Застолбить доставку `(message_id, telegram_user_id)` (status='pending').

        `ON CONFLICT (message_id, telegram_user_id) DO NOTHING`; пустой RETURNING →
        None (доставка уже заведена/доставлена — пропустить). Иначе — id новой строки.
        """
        stmt = (
            pg_insert(MailTelegramNotification)
            .values(message_id=message_id, telegram_user_id=telegram_user_id, status="pending")
            .on_conflict_do_nothing(constraint="uq_mail_tg_notif_msg_chat")
            .returning(MailTelegramNotification.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def mark_sent(self, notification_id: uuid.UUID) -> None:
        """Финализировать доставку: status='sent', `sent_at=now()`."""
        await self._session.execute(
            update(MailTelegramNotification)
            .where(MailTelegramNotification.id == notification_id)
            .values(status="sent", sent_at=datetime.now(UTC))
        )

    async def mark_failed(self, notification_id: uuid.UUID, error: str) -> None:
        """Транзиентный сбой: status='failed', attempts+1, last_error (проход B добьёт)."""
        await self._session.execute(
            update(MailTelegramNotification)
            .where(MailTelegramNotification.id == notification_id)
            .values(
                status="failed",
                attempts=MailTelegramNotification.attempts + 1,
                last_error=error[:500],
            )
        )

    async def mark_dead(self, notification_id: uuid.UUID, error: str) -> None:
        """Перманентный сбой/исчерпание попыток: status='dead', attempts+1, last_error."""
        await self._session.execute(
            update(MailTelegramNotification)
            .where(MailTelegramNotification.id == notification_id)
            .values(
                status="dead",
                attempts=MailTelegramNotification.attempts + 1,
                last_error=error[:500],
            )
        )

    async def pending_for_retry(
        self, *, max_attempts: int, limit: int
    ) -> list[PendingNotification]:
        """Кандидаты прохода B: `status IN (pending,failed) AND attempts < :max`.

        ORDER BY `updated_at` (старейшие первыми), LIMIT `:limit`.
        """
        stmt = (
            select(
                MailTelegramNotification.id,
                MailTelegramNotification.message_id,
                MailTelegramNotification.telegram_user_id,
                MailTelegramNotification.attempts,
            )
            .where(
                MailTelegramNotification.status.in_(("pending", "failed")),
                MailTelegramNotification.attempts < max_attempts,
            )
            .order_by(MailTelegramNotification.updated_at)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            PendingNotification(
                id=row_id,
                message_id=int(mid),
                telegram_user_id=int(tg),
                attempts=int(att),
            )
            for row_id, mid, tg, att in rows
        ]
