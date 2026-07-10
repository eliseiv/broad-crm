"""Приём push'а агрегатор→CRM: письма (`/ingest`) + статус ящика (`/mailbox-status`).

ADR-044 §3. Приём **быстрый, синхронный, только дешёвый SQL**: (1) insert письма
`ON CONFLICT DO NOTHING`; (2) если вставлено — применить теги (§5, best-effort).
**Telegram-рассылку приём НЕ делает** — письмо остаётся `notified_at IS NULL`, его
берёт фоновый диспетчер (S4). Неизвестный ящик — письмо пропускается
(`unknown_mailbox`), батч не отклоняется (иначе агрегатор ретраил бы вечно).
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import validation_error
from app.logging import get_logger
from app.repositories.mail_account_repository import MailAccountRepository
from app.repositories.mail_message_repository import MailMessageRepository
from app.repositories.mail_tag_repository import MailTagRepository
from app.schemas.mail_ingest import (
    MailboxStatusRequest,
    MailIngestRequest,
    MailIngestResponse,
)

logger = get_logger(__name__)


class MailIngestService:
    """Идемпотентный приём писем + зеркалирование статуса ящика (push-каналы)."""

    def __init__(self, session: AsyncSession, *, max_batch: int) -> None:
        self._session = session
        self._max_batch = max_batch

    async def ingest(self, request: MailIngestRequest) -> MailIngestResponse:
        """Принять батч писем идемпотентно (ADR-044 §3).

        Батч `1..MAIL_INGEST_MAX_BATCH`; вне диапазона → 400 validation_error. На каждое
        письмо: unknown-ящик → skip; insert `ON CONFLICT DO NOTHING`; вставлено → теги
        (best-effort, savepoint). Ответ — счётчики accepted/duplicate/unknown_mailbox.
        """
        messages = request.messages
        if not messages:
            raise validation_error("Пустой батч писем")
        if len(messages) > self._max_batch:
            raise validation_error(f"Размер батча превышает лимит {self._max_batch}")

        session = self._session
        accounts = MailAccountRepository(session)
        repo = MailMessageRepository(session)
        tags = MailTagRepository(session)

        existing = await accounts.existing_ids(m.mail_account_id for m in messages)
        # Закрыть autobegun read-tx перед явными write-транзакциями по письмам.
        await session.commit()

        accepted = 0
        duplicate = 0
        unknown = 0

        for message in messages:
            if message.mail_account_id not in existing:
                unknown += 1
                logger.warning(
                    "mail_ingest_unknown_mailbox", mail_account_id=message.mail_account_id
                )
                continue
            async with session.begin():
                inserted_id = await repo.insert_on_conflict(message)
                if inserted_id is None:
                    duplicate += 1
                    continue
                accepted += 1
                # Применение тегов — best-effort в savepoint: сбой не откатывает письмо.
                try:
                    async with session.begin_nested():
                        await tags.apply_tags_to_message(
                            message_id=inserted_id,
                            subject=message.subject,
                            body_text=message.body_text,
                            body_html=message.body_html,
                            from_addr=message.from_addr,
                            from_name=message.from_name,
                        )
                except SQLAlchemyError:
                    logger.warning("mail_ingest_tag_apply_failed", message_id=inserted_id)

        return MailIngestResponse(accepted=accepted, duplicate=duplicate, unknown_mailbox=unknown)

    async def apply_mailbox_status(self, payload: MailboxStatusRequest) -> bool:
        """Зеркалировать статус синка ящика (status-канал §3); guarded down-alert reset.

        Неизвестный `mail_account_id` → no-op (`updated=false`, аномалия TD-041), запрос
        не отклоняется. Идемпотентность алерта — на стороне CRM (`down_alert_sent_at`);
        сам алерт шлёт диспетчер (проход C, S4).
        """
        session = self._session
        accounts = MailAccountRepository(session)
        async with session.begin():
            account = await accounts.get(payload.mail_account_id)
            if account is None:
                logger.warning(
                    "mail_status_unknown_mailbox", mail_account_id=payload.mail_account_id
                )
                return False
            await accounts.apply_sync_status(
                account,
                is_active=payload.is_active,
                last_synced_at=payload.last_synced_at,
                last_sync_error=payload.last_sync_error,
                consecutive_failures=payload.consecutive_failures,
            )
        return True
