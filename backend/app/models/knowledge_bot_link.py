"""Модель таблицы `knowledge_bot_links` (03-data-model.md, ADR-076).

Факт «сотрудник написал Telegram ИИ-боту базы знаний» + chat_id доставки рассылки.
PK — `telegram_user_id BIGINT` (атомарный upsert `ON CONFLICT DO UPDATE`).
`user_id → users.id ON DELETE CASCADE` (1:N, без UNIQUE). Активна ⇔ `dead_at IS NULL`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeBotLink(Base):
    """Привязка Telegram-аккаунта к CRM-пользователю для ИИ-бота (ADR-076)."""

    __tablename__ = "knowledge_bot_links"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
