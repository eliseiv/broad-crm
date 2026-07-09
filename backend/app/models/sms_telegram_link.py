"""Модель таблицы `sms_telegram_links` (03-data-model.md, ADR-030).

Привязка CRM-пользователь ↔ Telegram-аккаунт (одна строка на привязанный чат).
PK — `telegram_user_id BIGINT` (атомарный upsert `ON CONFLICT DO UPDATE`).
`user_id → users.id ON DELETE CASCADE` (1:N, без UNIQUE — один юзер может иметь
несколько привязок). Активна пока `dead_at IS NULL` (bot заблокирован → `dead_at`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SmsTelegramLink(Base):
    """Привязка Telegram-аккаунта оператора к CRM-пользователю (ADR-030)."""

    __tablename__ = "sms_telegram_links"

    # Внешний chat_id Telegram-аккаунта (upsert `ON CONFLICT`), НЕ autoincrement/serial.
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
