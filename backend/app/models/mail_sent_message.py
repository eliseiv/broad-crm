"""Модель таблицы `mail_sent_messages` — запись отправленных reply (ADR-044 §2/§8).

CRM теперь инициатор отправки: письмо живёт в CRM, threading-заголовки формирует CRM,
SMTP-отправку делегирует агрегатору (`POST /api/external/mailboxes/{id}/send`). После
успешной отправки запись сохраняется здесь (аудит/история). Креды в CRM не хранятся —
только факт отправки и её метаданные. `smtp_message_id` — id письма из ответа агрегатора.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailSentMessage(Base):
    """Отправленный reply (запись инициатора-CRM, ADR-044 §2/§8)."""

    __tablename__ = "mail_sent_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    mail_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("mail_accounts.id", ondelete="CASCADE", name="fk_mail_sent_messages_account_id"),
        nullable=False,
    )
    # Автор отправки (CRM-пользователь); SET NULL при удалении пользователя.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_mail_sent_messages_user_id"),
        nullable=True,
    )
    to_addrs: Mapped[str] = mapped_column(Text, nullable=False)
    cc_addrs: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    in_reply_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    refs_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    smtp_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
