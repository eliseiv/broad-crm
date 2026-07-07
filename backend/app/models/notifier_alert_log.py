"""Модель таблицы `notifier_alert_log` (03-data-model.md, ADR-018).

Append-only durable-лог отправленных серверных алертов Telegram-нотификатора: одна
строка на каждый вызов `TelegramClient.send_message` с фактом доставки (`delivered`).
Переживает ротацию stdout-логов и удаление сервера (`ON DELETE SET NULL` — история
алертов сохраняется, `server_id` обнуляется). Отличается от `notifier_server_state`
(эфемерное состояние дедупа, `ON DELETE CASCADE`) намеренно. `bigint identity` PK —
осознанное отклонение от uuid-конвенции для append-only ops-лога (ADR-018).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NotifierAlertLog(Base):
    """Строка durable-лога одного отправленного серверного алерта."""

    __tablename__ = "notifier_alert_log"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('offline','recovered','warning','critical')",
            name="ck_notifier_alert_log_kind",
        ),
        Index("ix_notifier_alert_log_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=False),
        primary_key=True,
    )
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("servers.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
