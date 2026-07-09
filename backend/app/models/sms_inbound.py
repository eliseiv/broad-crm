"""Модель таблицы `sms_inbound` (03-data-model.md, ADR-030).

Входящие SMS. Связь с номером — **по строке `to_number`** (не FK): удаление номера
сохраняет историю SMS. PK — `BIGINT IDENTITY` (позиция keyset-курсора ленты по
`(received_at, id)`). `team_id` — **снимок** команды на момент приёма (определяет
получателей fan-out), `ON DELETE SET NULL`; для отображения используется текущий
номер, не снимок (ADR-030 §6). `raw_payload` — полное тело Twilio-webhook (аудит).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SmsInbound(Base):
    """Входящее SMS (история; связь с номером по `to_number`, ADR-030)."""

    __tablename__ = "sms_inbound"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    twilio_message_sid: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_number: Mapped[str] = mapped_column(Text, nullable=False)
    to_number: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
