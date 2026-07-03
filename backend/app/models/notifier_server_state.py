"""Модель таблицы `notifier_server_state` (03-data-model.md, ADR-014).

Персистентное состояние Telegram-нотификатора per-server: последняя наблюдённая
доступность (`online`) и зеркало зон трёх метрик (`zone_cpu/ram/ssd`). Переживает
рестарт/деплой backend (закрывает TD-019). 1:1 к `servers` (PK = FK, ON DELETE
CASCADE). Семантика переходов и alert-on-first-elevated — modules/notifier.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NotifierServerState(Base):
    """Состояние нотификатора одного сервера (зеркало логической формы ServerState)."""

    __tablename__ = "notifier_server_state"
    __table_args__ = (
        CheckConstraint(
            "zone_cpu IN ('green','yellow','red')",
            name="ck_notifier_server_state_zone_cpu",
        ),
        CheckConstraint(
            "zone_ram IN ('green','yellow','red')",
            name="ck_notifier_server_state_zone_ram",
        ),
        CheckConstraint(
            "zone_ssd IN ('green','yellow','red')",
            name="ck_notifier_server_state_zone_ssd",
        ),
    )

    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    online: Mapped[bool] = mapped_column(Boolean, nullable=False)
    zone_cpu: Mapped[str | None] = mapped_column(Text, nullable=True)
    zone_ram: Mapped[str | None] = mapped_column(Text, nullable=True)
    zone_ssd: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
