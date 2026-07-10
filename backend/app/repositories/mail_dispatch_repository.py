"""Диспетчерские запросы Telegram-доставки почты (ADR-044 §6). Проходы A/B/C.

Выделен из `mail_message_repository`/`mail_account_repository`, чтобы фоновый
диспетчер (S4) не пересекался с чтением ленты/приёмом push'а. Содержит: выборку
новых писем (`notified_at IS NULL`, проход A), high-water `mark_notified`, загрузку
письма/тегов для форматирования (в т.ч. на recovery), выборку упавших ящиков
(`is_active=false AND down_alert_sent_at IS NULL`, проход C) и guarded-штамп
`down_alert_sent_at` («ровно один алерт на переход»).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_account import MailAccount
from app.models.mail_message import MailMessage


@dataclass(frozen=True, slots=True)
class DispatchMessage:
    """Снимок письма + метаданные ящика для форматирования уведомления."""

    id: int
    mail_account_id: int
    team_id: uuid.UUID | None
    acc_label: str
    subject: str | None
    from_addr: str
    from_name: str | None
    body_text: str
    body_html: str | None


@dataclass(frozen=True, slots=True)
class DownMailbox:
    """Упавший ящик — кандидат mailbox-down алерта (проход C)."""

    id: int
    team_id: uuid.UUID | None
    acc_label: str
    last_sync_error: str | None


class MailDispatchRepository:
    """Читатель/писатель диспетчера: очередь писем, теги, статус ящика."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def unnotified_message_ids(self, limit: int) -> list[int]:
        """id новых писем (`notified_at IS NULL`) в порядке `id` (проход A, очередь)."""
        stmt = (
            select(MailMessage.id)
            .where(MailMessage.notified_at.is_(None))
            .order_by(MailMessage.id)
            .limit(limit)
        )
        return [int(mid) for mid in (await self._session.execute(stmt)).scalars().all()]

    async def load_dispatch_message(self, message_id: int) -> DispatchMessage | None:
        """Загрузить письмо + метаданные ящика (label/team) для форматирования."""
        stmt = (
            select(
                MailMessage.id,
                MailMessage.mail_account_id,
                MailAccount.team_id,
                MailAccount.display_name,
                MailAccount.email,
                MailMessage.subject,
                MailMessage.from_addr,
                MailMessage.from_name,
                MailMessage.body_text,
                MailMessage.body_html,
            )
            .join(MailAccount, MailAccount.id == MailMessage.mail_account_id)
            .where(MailMessage.id == message_id)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        (
            mid,
            acc_id,
            team_id,
            display_name,
            email,
            subject,
            from_addr,
            from_name,
            body_text,
            body_html,
        ) = row
        return DispatchMessage(
            id=int(mid),
            mail_account_id=int(acc_id),
            team_id=team_id,
            acc_label=display_name or email,
            subject=subject,
            from_addr=from_addr,
            from_name=from_name,
            body_text=body_text,
            body_html=body_html,
        )

    async def tag_names_for_message(self, message_id: int) -> list[str]:
        """Имена тегов письма (для строки #️⃣), стабильный порядок по имени."""
        stmt = text(
            """
            SELECT t.name
            FROM   mail_message_tags mt
            JOIN   mail_tags t ON t.id = mt.tag_id
            WHERE  mt.message_id = :message_id
            ORDER  BY t.name
            """
        )
        result = await self._session.execute(stmt, {"message_id": message_id})
        return [str(row.name) for row in result]

    async def mark_notified(self, message_id: int) -> None:
        """High-water: пометить письмо обработанным (`notified_at = now()`)."""
        await self._session.execute(
            update(MailMessage)
            .where(MailMessage.id == message_id)
            .values(notified_at=datetime.now(UTC))
        )

    async def down_mailboxes(self, limit: int) -> list[DownMailbox]:
        """Упавшие ящики без разосланного алерта (проход C)."""
        stmt = (
            select(
                MailAccount.id,
                MailAccount.team_id,
                MailAccount.display_name,
                MailAccount.email,
                MailAccount.last_sync_error,
            )
            .where(
                MailAccount.is_active.is_(False),
                MailAccount.down_alert_sent_at.is_(None),
            )
            .order_by(MailAccount.id)
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            DownMailbox(
                id=int(acc_id),
                team_id=team_id,
                acc_label=display_name or email,
                last_sync_error=last_sync_error,
            )
            for acc_id, team_id, display_name, email, last_sync_error in rows
        ]

    async def try_stamp_down_alert(self, mail_account_id: int) -> bool:
        """Guarded-штамп `down_alert_sent_at=now()` (идемпотентность «один на переход»).

        `WHERE id=:id AND is_active=false AND down_alert_sent_at IS NULL`. True, если
        строка обновлена (этот вызов «выиграл» право разослать алерт).
        """
        result = await self._session.execute(
            update(MailAccount)
            .where(
                MailAccount.id == mail_account_id,
                MailAccount.is_active.is_(False),
                MailAccount.down_alert_sent_at.is_(None),
            )
            .values(down_alert_sent_at=datetime.now(UTC))
        )
        return int(result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def message_visibility(self, message_id: int) -> tuple[uuid.UUID | None, bool] | None:
        """(team_id ящика письма, существует ли письмо) — для callback visibility.

        None → письмо не найдено. Иначе (team_id | None, True).
        """
        stmt = (
            select(MailAccount.team_id)
            .join(MailMessage, MailMessage.mail_account_id == MailAccount.id)
            .where(MailMessage.id == message_id)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return row[0], True
