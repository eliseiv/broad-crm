"""Модель таблицы `sms_deliveries` (03-data-model.md, ADR-030).

Реестр доставок SMS операторам в Telegram (одна строка на пару SMS × получатель).
PK — `BIGINT IDENTITY`. `inbound_sms_id → sms_inbound.id ON DELETE CASCADE`;
`user_id → users.id ON DELETE CASCADE`. `telegram_user_id` — снимок chat_id на
момент доставки (без FK — реестр переживает удаление/перепривязку линка). UNIQUE
`(inbound_sms_id, telegram_user_id)` даёт идемпотентность fan-out (`try_reserve`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SmsDelivery(Base):
    """Доставка SMS одному оператору в Telegram (статус/попытки, ADR-030)."""

    __tablename__ = "sms_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','sent','failed','dead')",
            name="ck_sms_deliveries_status",
        ),
        UniqueConstraint(
            "inbound_sms_id",
            "telegram_user_id",
            name="uq_sms_deliveries_sms_chat",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    inbound_sms_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sms_inbound.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
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
