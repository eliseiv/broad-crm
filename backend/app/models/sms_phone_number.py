"""Модель таблицы `sms_phone_numbers` (03-data-model.md, ADR-030).

Реестр Twilio-номеров, привязанных к CRM-командам. Номера появляются автоматически
(входящие SMS + `POST /api/sms/numbers/sync`); вручную не создаются. **Асимметрия
ключей (ADR-030 §2):** собственный PK — `BIGINT IDENTITY` (донорская идиома);
внешние ссылки на команды/пользователей — `UUID` (FK на CRM `teams`/`users`).

`team_id → teams.id ON DELETE SET NULL` (NULL = unassigned-пул; удаление команды →
номер в пул); `added_by_user_id → users.id ON DELETE SET NULL`. `label` — системный
никнейм (зеркало Twilio `friendly_name`, правится только `sync`); `login`/`app_name`/
`note` — редактируемые пользователем поля (`PATCH`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.team import Team


class SmsPhoneNumber(Base):
    """Реестр SMS-номеров (Twilio-номера ↔ CRM-команды, ADR-030)."""

    __tablename__ = "sms_phone_numbers"
    __table_args__ = (UniqueConstraint("phone_number", name="uq_sms_phone_numbers_phone_number"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    phone_number: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    login: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Текущая команда-владелец (для бейджа/фильтра/`SmsTeamRef`). viewonly —
    # принадлежность меняется явными statements (transfer). Единственная FK на
    # teams делает join-условие однозначным.
    team: Mapped[Team | None] = relationship("Team", lazy="select", viewonly=True)
