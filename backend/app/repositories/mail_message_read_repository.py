"""Репозиторий `mail_message_reads` — личное состояние писем (ADR-050, ADR-071)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_message import MailMessage
from app.models.mail_message_read import MailMessageRead


class MailMessageReadRepository:
    """Личные отметки: прочитанность, архив, корзина."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_ids(self, *, user_id: uuid.UUID, message_ids: Sequence[int]) -> set[int]:
        """Подмножество `message_ids` с `read_at IS NOT NULL`."""
        if not message_ids:
            return set()
        stmt = select(MailMessageRead.message_id).where(
            MailMessageRead.user_id == user_id,
            MailMessageRead.message_id.in_(message_ids),
            MailMessageRead.read_at.is_not(None),
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def mark_read(self, *, user_id: uuid.UUID, message_id: int) -> None:
        """Пометить прочитанным; `read_at` при повторе не обновляется."""
        now = datetime.now(UTC)
        ins = pg_insert(MailMessageRead).values(user_id=user_id, message_id=message_id, read_at=now)
        stmt = ins.on_conflict_do_update(
            constraint="pk_mail_message_reads",
            set_={"read_at": func.coalesce(MailMessageRead.read_at, ins.excluded.read_at)},
        )
        await self._session.execute(stmt)

    async def unmark_read(self, *, user_id: uuid.UUID, message_id: int) -> None:
        """Вернуть в непрочитано: удалить строку без archive/delete, иначе `read_at=NULL`."""
        row = await self._session.get(
            MailMessageRead, {"user_id": user_id, "message_id": message_id}
        )
        if row is None:
            return
        if row.archived_at is None and row.deleted_at is None:
            await self._session.delete(row)
        else:
            row.read_at = None

    async def mark_archived(self, *, user_id: uuid.UUID, message_ids: Sequence[int]) -> None:
        now = datetime.now(UTC)
        for message_id in message_ids:
            stmt = (
                pg_insert(MailMessageRead)
                .values(
                    user_id=user_id,
                    message_id=message_id,
                    archived_at=now,
                    deleted_at=None,
                )
                .on_conflict_do_update(
                    constraint="pk_mail_message_reads",
                    set_={"archived_at": now, "deleted_at": None},
                )
            )
            await self._session.execute(stmt)

    async def mark_deleted(self, *, user_id: uuid.UUID, message_ids: Sequence[int]) -> None:
        now = datetime.now(UTC)
        for message_id in message_ids:
            stmt = (
                pg_insert(MailMessageRead)
                .values(user_id=user_id, message_id=message_id, deleted_at=now)
                .on_conflict_do_update(
                    constraint="pk_mail_message_reads",
                    set_={"deleted_at": now},
                )
            )
            await self._session.execute(stmt)

    async def mark_unarchived(self, *, user_id: uuid.UUID, message_ids: Sequence[int]) -> None:
        stmt = (
            update(MailMessageRead)
            .where(
                MailMessageRead.user_id == user_id,
                MailMessageRead.message_id.in_(message_ids),
            )
            .values(archived_at=None)
        )
        await self._session.execute(stmt)

    async def mark_restored(self, *, user_id: uuid.UUID, message_ids: Sequence[int]) -> None:
        stmt = (
            update(MailMessageRead)
            .where(
                MailMessageRead.user_id == user_id,
                MailMessageRead.message_id.in_(message_ids),
            )
            .values(deleted_at=None)
        )
        await self._session.execute(stmt)

    async def batch_mark_read(self, *, user_id: uuid.UUID, message_ids: Sequence[int]) -> None:
        for message_id in message_ids:
            await self.mark_read(user_id=user_id, message_id=message_id)

    async def count_unread_inbox(
        self,
        *,
        user_id: uuid.UUID,
        mail_account_ids: list[int] | None,
    ) -> int:
        """COUNT непрочитанных в inbox для видимых ящиков."""
        if mail_account_ids is not None and len(mail_account_ids) == 0:
            return 0
        inbox_filter = (
            ~select(MailMessageRead.message_id)
            .where(
                MailMessageRead.user_id == user_id,
                MailMessageRead.message_id == MailMessage.id,
                (MailMessageRead.archived_at.is_not(None))
                | (MailMessageRead.deleted_at.is_not(None)),
            )
            .exists()
        )
        unread_filter = (
            ~select(MailMessageRead.message_id)
            .where(
                MailMessageRead.user_id == user_id,
                MailMessageRead.message_id == MailMessage.id,
                MailMessageRead.read_at.is_not(None),
            )
            .exists()
        )
        stmt = select(func.count()).select_from(MailMessage).where(inbox_filter, unread_filter)
        if mail_account_ids is not None:
            stmt = stmt.where(MailMessage.mail_account_id.in_(mail_account_ids))
        return int((await self._session.execute(stmt)).scalar_one())
