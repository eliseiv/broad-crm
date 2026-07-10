"""Модель таблицы `mail_accounts` — локальный каталог ящиков (ADR-044 §2).

CRM — источник истины привязки ящик↔команда (`team_id`). `id` **равен** id ящика в
агрегаторе (агрегатор присваивает при создании; единый int-ключ связывает письма
push'а). Поля синка (`is_active`/`last_synced_at`/`last_sync_error`/
`consecutive_failures`) — зеркало статуса из агрегатора, обновляются status-каналом
`POST /api/mail/mailbox-status`. `down_alert_sent_at` — идемпотентность mailbox-down
алерта «ровно один на переход» (guarded `WHERE ... IS NULL`; reset в NULL на re-enable).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailAccount(Base):
    """Каталог почтовых ящиков CRM (per-mailbox `team_id`, ADR-044 §2)."""

    __tablename__ = "mail_accounts"

    # id == id ящика в агрегаторе (НЕ autoincrement — присваивается агрегатором).
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Команда-владелец (per-mailbox). NULL = ящик без команды (пул unassigned).
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL", name="fk_mail_accounts_team_id"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Идемпотентность mailbox-down алерта «ровно один на переход» (проход C, §6).
    down_alert_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index("ix_mail_accounts_team_id", MailAccount.team_id)
