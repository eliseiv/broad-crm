"""Модель таблицы `mail_user_settings` — opt-out Telegram-уведомлений (ADR-044 §2, MAJOR-4).

По образцу агрегаторского `users_settings`. Дефолт (нет строки) = уведомления включены.
Правится через `PATCH /api/mail/me/settings` (S4). Механизм обязан существовать (иначе
после переезда пользователь не сможет отписаться — регресс).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailUserSettings(Base):
    """Настройки уведомлений пользователя (opt-out, ADR-044 §2)."""

    __tablename__ = "mail_user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_mail_user_settings_user_id"),
        primary_key=True,
    )
    tg_notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
