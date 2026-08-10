"""Репозиторий записей отправленных reply `mail_sent_messages` (ADR-044, ADR-071)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_sent_message import MailSentMessage


class MailSentMessageRepository:
    """Запись и листинг отправленных reply."""

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

    async def get(self, sent_id: uuid.UUID) -> MailSentMessage | None:
        return await self._session.get(MailSentMessage, sent_id)

    async def list_feed(
        self,
        *,
        mail_account_ids: list[int] | None,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[MailSentMessage]:
        if mail_account_ids is not None and len(mail_account_ids) == 0:
            return []
        stmt = select(MailSentMessage)
        if mail_account_ids is not None:
            stmt = stmt.where(MailSentMessage.mail_account_id.in_(mail_account_ids))
        if cursor is not None:
            s0, id0 = cursor
            stmt = stmt.where(
                or_(
                    MailSentMessage.sent_at < s0,
                    and_(MailSentMessage.sent_at == s0, MailSentMessage.id < id0),
                )
            )
        stmt = stmt.order_by(MailSentMessage.sent_at.desc(), MailSentMessage.id.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())
