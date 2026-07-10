"""Репозиторий записей отправленных reply `mail_sent_messages` (ADR-044 §2/§8).

CRM — инициатор отправки: после успешного делегирования SMTP-отправки агрегатору
факт отправки сохраняется здесь (аудит/история). Креды не хранятся.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_sent_message import MailSentMessage


class MailSentMessageRepository:
    """Запись отправленных reply (append-only история отправки)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        mail_account_id: int,
        user_id: uuid.UUID | None,
        to_addrs: str,
        cc_addrs: str | None,
        subject: str | None,
        body_text: str,
        in_reply_to: str | None,
        refs_header: str | None,
        smtp_message_id: str | None,
    ) -> MailSentMessage:
        """Сохранить факт отправки reply (ADR-044 §8)."""
        sent = MailSentMessage(
            mail_account_id=mail_account_id,
            user_id=user_id,
            to_addrs=to_addrs,
            cc_addrs=cc_addrs,
            subject=subject,
            body_text=body_text,
            in_reply_to=in_reply_to,
            refs_header=refs_header,
            smtp_message_id=smtp_message_id,
        )
        self._session.add(sent)
        await self._session.flush()
        return sent
