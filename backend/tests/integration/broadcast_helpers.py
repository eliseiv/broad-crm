"""Инфраструктура integration-тестов broadcast / knowledge-bot (ADR-076).

Переиспользует `sms_db`/`build_app`/`client`/`seed_*` (реальный Postgres, create_all).
TRUNCATE users CASCADE снимает `knowledge_bot_links` и mail/sms-линки.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.infra.knowledge_bot_telegram import TelegramApiError, TelegramForbiddenError
from app.models.knowledge_bot_link import KnowledgeBotLink
from app.models.mail_telegram import MailTelegramLink
from sqlalchemy.ext.asyncio import AsyncSession

from sms_helpers import (  # noqa: F401  (реэкспорт)
    build_app,
    build_principal,
    client,
    seed_link as seed_sms_link,
    seed_role,
    seed_user,
    sms_db,
)


class FakeKnowledgeBot:
    """Мок Bot API ИИ-бота: успех / 403 → Forbidden / прочая ошибка."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self._forbidden: set[int] = set()
        self._errors: set[int] = set()

    def forbidden_for(self, chat_id: int) -> None:
        self._forbidden.add(chat_id)

    def error_for(self, chat_id: int) -> None:
        self._errors.add(chat_id)

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        if chat_id in self._forbidden:
            raise TelegramForbiddenError(f"forbidden:{chat_id}")
        if chat_id in self._errors:
            raise TelegramApiError(f"api:{chat_id}")
        self.sent.append((chat_id, text))
        return {"ok": True, "result": {"message_id": len(self.sent)}}


async def seed_knowledge_link(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    user_id: uuid.UUID,
    username: str | None = None,
    dead_at: datetime | None = None,
    started_at: datetime | None = None,
) -> KnowledgeBotLink:
    link = KnowledgeBotLink(
        telegram_user_id=telegram_user_id,
        user_id=user_id,
        username=username,
        dead_at=dead_at,
        started_at=started_at or datetime.now(UTC),
    )
    session.add(link)
    await session.flush()
    return link


async def seed_mail_link(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    user_id: uuid.UUID | None = None,
    username: str | None = None,
    dead_at: datetime | None = None,
) -> MailTelegramLink:
    link = MailTelegramLink(
        telegram_user_id=telegram_user_id,
        user_id=user_id,
        username=username,
        dead_at=dead_at,
    )
    session.add(link)
    await session.flush()
    return link


def enable_knowledge_bot(monkeypatch: Any, token: str = "kb-test-token") -> None:
    monkeypatch.setenv("KNOWLEDGE_BOT_TOKEN", token)
    from app.config import get_settings

    get_settings.cache_clear()


def configure_documents_key(monkeypatch: Any, value: str = "secret-external-key-123") -> None:
    monkeypatch.setenv("DOCUMENTS_API_KEY", value)
    from app.config import get_settings

    get_settings.cache_clear()
