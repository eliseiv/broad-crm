"""Репозиторий писем `mail_messages` (ADR-044 §2/§3).

Идемпотентный приём push'а — `INSERT ... ON CONFLICT (mail_account_id, uidvalidity,
uid) DO NOTHING RETURNING id` (повтор доставки не дублирует письмо). Лента — **компаундный
keyset** по паре `(internal_date, id)` (MINOR-2): `internal_date` не уникален (массовая
рассылка приходит одной секундой), сортировка/пагинация только по нему дала бы пропуски
и дубли на границах страниц. Курсор несёт обе компоненты, сравнение row-wise.

Фильтр «только непрочитанные» (ADR-050 §2.4) применяется **ВНУТРИ** этого keyset-запроса
анти-джойном `NOT EXISTS` (резолвится по PK `mail_message_reads (user_id, message_id)`):
клиентская фильтрация запрещена — лента курсорная, фильтр над загруженным набором ломает
догрузку. Порядок и формат курсора при этом не меняются. Обратное — поле `is_unread`: оно
считается ВТОРЫМ батч-запросом по уже отобранной странице (`MailMessageReadRepository`), а
не JOIN'ом сюда (план этого запроса держится на индексе `(internal_date DESC, id DESC)`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_message import MailMessage
from app.models.mail_message_read import MailMessageRead
from app.schemas.mail_ingest import MailIngestMessage


class MailMessageRepository:
    """Идемпотентный приём писем + компаундный keyset-листинг ленты."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, message_id: int) -> MailMessage | None:
        """Письмо по id (для reply: threading-заголовки + `mail_account_id`) или None."""
        return await self._session.get(MailMessage, message_id)

    async def insert_on_conflict(self, message: MailIngestMessage) -> int | None:
        """Вставляет письмо идемпотентно; возвращает `id` (вставлено) или None (дубль).

        Ключ идемпотентности — `uq_mail_messages_account_uidv_uid`. Новое письмо
        приходит с `notified_at IS NULL` (диспетчер S4 разошлёт) — на приёме НЕ
        выставляется (ADR-044 §3).
        """
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
    ) -> list[MailMessage]:
        """Компаундный keyset-листинг ленты (ADR-044 §2, порядок `internal_date DESC, id DESC`).

        `mail_account_ids` — набор видимых ящиков (`mail_account_id IN (...)`): `None` —
        без фильтра (admin-scope, все письма); пустой список — пустой результат (без
        запроса, анти-энумерация). `cursor` — позиция `(internal_date, id)` для предиката
        `(internal_date, id) < (d0, id0)`. Вызывающий передаёт `limit + 1` для определения
        следующей страницы.

        `unread_for_user_id` (ADR-050 §2.4) — фильтр «только непрочитанные ЭТИМ
        пользователем»: анти-джойн `NOT EXISTS (mail_message_reads)` ВНУТРИ запроса (иначе
        фильтрация над уже загруженной страницей ломала бы курсорную догрузку). `None` —
        фильтр не применяется.
        """
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
