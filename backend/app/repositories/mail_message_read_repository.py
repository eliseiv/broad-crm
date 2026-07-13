"""Репозиторий `mail_message_reads` — личная прочитанность писем (ADR-050 §2).

Существование строки = «прочитано ЭТИМ пользователем». Оба горячих пути ленты идут по PK
`(user_id, message_id)`:

- `read_ids` — **батч-лукап по уже отобранной странице** (`message_id = ANY(:page_ids)`):
  один запрос на страницу (≤200 ключей), **не** N+1 и **не** JOIN в keyset-запрос ленты
  (его план держится на индексе `(internal_date DESC, id DESC)`, ADR-050 §2.4);
- анти-джойн фильтра `unread=true` живёт в `MailMessageRepository.list_feed` — **внутри**
  keyset-запроса (клиентская фильтрация запрещена: сломала бы курсорную догрузку).

`mark_read` идемпотентен (`ON CONFLICT DO NOTHING`): при повторе `read_at` НЕ обновляется
(важно первое открытие). `unmark_read` идемпотентен: строки нет — не ошибка.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_message_read import MailMessageRead


class MailMessageReadRepository:
    """Личные отметки прочитанности (батч-лукап + идемпотентные mark/unmark)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_ids(self, *, user_id: uuid.UUID, message_ids: Sequence[int]) -> set[int]:
        """Подмножество `message_ids`, прочитанных пользователем (один запрос по PK)."""
        if not message_ids:
            return set()
        stmt = select(MailMessageRead.message_id).where(
            MailMessageRead.user_id == user_id,
            MailMessageRead.message_id.in_(message_ids),
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def mark_read(self, *, user_id: uuid.UUID, message_id: int) -> None:
        """Пометить прочитанным (идемпотентно; `read_at` при повторе не обновляется)."""
        stmt = (
            pg_insert(MailMessageRead)
            .values(user_id=user_id, message_id=message_id)
            .on_conflict_do_nothing(constraint="pk_mail_message_reads")
        )
        await self._session.execute(stmt)

    async def unmark_read(self, *, user_id: uuid.UUID, message_id: int) -> None:
        """Вернуть в «непрочитано» (идемпотентно: строки не было — не ошибка)."""
        stmt = delete(MailMessageRead).where(
            MailMessageRead.user_id == user_id,
            MailMessageRead.message_id == message_id,
        )
        await self._session.execute(stmt)
