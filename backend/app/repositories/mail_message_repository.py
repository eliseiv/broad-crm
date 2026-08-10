"""Репозиторий писем `mail_messages` (ADR-044 §2/§3, ADR-071)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_message import MailMessage
from app.models.mail_message_read import MailMessageRead
from app.models.mail_tag import MailMessageTag
from app.schemas.mail_ingest import MailIngestMessage

MailFolder = Literal["inbox", "archived", "deleted"]


class MailMessageRepository:
    """Идемпотентный приём писем + компаундный keyset-листинг ленты."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, message_id: int) -> MailMessage | None:
        return await self._session.get(MailMessage, message_id)

    async def insert_on_conflict(self, message: MailIngestMessage) -> int | None:
        stmt = (
            pg_insert(MailMessage)
            .values(
                mail_account_id=message.mail_account_id,
                uidvalidity=message.uidvalidity,
                uid=message.uid,
                message_id_header=message.message_id_header,
                subject=message.subject,
                from_addr=message.from_addr,
                from_name=message.from_name,
                to_addrs=message.to_addrs,
                cc_addrs=message.cc_addrs,
                internal_date=message.internal_date,
                body_text=message.body_text,
                body_html=message.body_html,
                in_reply_to=message.in_reply_to,
                refs_header=message.refs_header,
            )
            .on_conflict_do_nothing(constraint="uq_mail_messages_account_uidv_uid")
            .returning(MailMessage.id)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_feed(
        self,
        *,
        mail_account_ids: list[int] | None,
        cursor: tuple[datetime, int] | None,
        limit: int,
        unread_for_user_id: uuid.UUID | None = None,
        folder_for_user_id: uuid.UUID | None = None,
        folder: MailFolder = "inbox",
        has_tags: bool | None = None,
        tag_id: uuid.UUID | None = None,
    ) -> list[MailMessage]:
        if mail_account_ids is not None and len(mail_account_ids) == 0:
            return []
        stmt = select(MailMessage)
        if mail_account_ids is not None:
            stmt = stmt.where(MailMessage.mail_account_id.in_(mail_account_ids))
        if unread_for_user_id is not None:
            stmt = stmt.where(
                ~select(MailMessageRead.message_id)
                .where(
                    MailMessageRead.message_id == MailMessage.id,
                    MailMessageRead.user_id == unread_for_user_id,
                    MailMessageRead.read_at.is_not(None),
                )
                .exists()
            )
        if folder_for_user_id is not None:
            if folder == "inbox":
                stmt = stmt.where(
                    ~select(MailMessageRead.message_id)
                    .where(
                        MailMessageRead.message_id == MailMessage.id,
                        MailMessageRead.user_id == folder_for_user_id,
                        or_(
                            MailMessageRead.archived_at.is_not(None),
                            MailMessageRead.deleted_at.is_not(None),
                        ),
                    )
                    .exists()
                )
            elif folder == "archived":
                stmt = stmt.where(
                    select(MailMessageRead.message_id)
                    .where(
                        MailMessageRead.message_id == MailMessage.id,
                        MailMessageRead.user_id == folder_for_user_id,
                        MailMessageRead.archived_at.is_not(None),
                        MailMessageRead.deleted_at.is_(None),
                    )
                    .exists()
                )
            elif folder == "deleted":
                stmt = stmt.where(
                    select(MailMessageRead.message_id)
                    .where(
                        MailMessageRead.message_id == MailMessage.id,
                        MailMessageRead.user_id == folder_for_user_id,
                        MailMessageRead.deleted_at.is_not(None),
                    )
                    .exists()
                )
        if has_tags:
            stmt = stmt.where(
                select(MailMessageTag.message_id)
                .where(MailMessageTag.message_id == MailMessage.id)
                .exists()
            )
        if tag_id is not None:
            stmt = stmt.where(
                select(MailMessageTag.message_id)
                .where(
                    MailMessageTag.message_id == MailMessage.id,
                    MailMessageTag.tag_id == tag_id,
                )
                .exists()
            )
        if cursor is not None:
            d0, id0 = cursor
            stmt = stmt.where(
                or_(
                    MailMessage.internal_date < d0,
                    and_(MailMessage.internal_date == d0, MailMessage.id < id0),
                )
            )
        stmt = stmt.order_by(MailMessage.internal_date.desc(), MailMessage.id.desc()).limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())
