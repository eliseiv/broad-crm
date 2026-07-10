"""Модели Telegram-слоя почты: `mail_telegram_links`, `mail_telegram_notifications`.

`mail_telegram_links` (ADR-044 §2): `telegram_user_id` (= `chat_id`) — стабильный ключ
**доставки** (Bot API шлёт только по числовому chat_id). `user_id` **NULLABLE** (orphan-
линк без CRM-пользователя, ленивый резолв §6). `username` — нормализованный lower-case
Telegram-username — ключ **первичного связывания**. `mail_telegram_notifications` —
дедуп доставки + история (`UNIQUE (message_id, telegram_user_id)` — «ровно один на
переход»). `telegram_user_id` в notifications — снапшот чата (без FK).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailTelegramLink(Base):
    """Привязка Telegram-аккаунта к CRM-пользователю (multi-link, orphan-резолв, §2/§6)."""

    __tablename__ = "mail_telegram_links"

    # telegram_user_id == chat_id (стабильный ключ доставки), НЕ autoincrement.
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # NULLABLE — orphan-линк без CRM-пользователя (ленивый резолв §6).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_mail_telegram_links_user_id"),
        nullable=True,
    )
    # Нормализованный lower-case Telegram-username — ключ первичного связывания.
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Резолв по связанным линкам / сверка orphan'ов (partial-индексы).
Index(
    "ix_mail_tg_links_user_id",
    MailTelegramLink.user_id,
    postgresql_where=text("user_id IS NOT NULL"),
)
Index(
    "ix_mail_tg_links_username",
    MailTelegramLink.username,
    postgresql_where=text("user_id IS NULL"),
)


class MailTelegramNotification(Base):
    """Дедуп доставки уведомления + история (ADR-044 §2)."""

    __tablename__ = "mail_telegram_notifications"
    __table_args__ = (
        UniqueConstraint("message_id", "telegram_user_id", name="uq_mail_tg_notif_msg_chat"),
        CheckConstraint(
            "status IN ('pending','sent','failed','dead')",
            name="ck_mail_tg_notif_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mail_messages.id", ondelete="CASCADE", name="fk_mail_tg_notif_message_id"),
        nullable=False,
    )
    # Снапшот чата (без FK — линк может исчезнуть, история остаётся).
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
