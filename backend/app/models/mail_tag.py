"""Модели тегов почты: `mail_tags`, `mail_tag_rules`, `mail_message_tags` (ADR-044 §2/§5).

Теги — **глобальный админский каталог** (у тега нет владельца): применяются ко всем
письмам всех команд. `UNIQUE (name)` — глобально уникальное имя (`tags.user_id`
агрегатора не переносится вовсе). `match_mode` any/all. `mail_message_tags` —
дедуп применения (`PRIMARY KEY (message_id, tag_id)`, `ON CONFLICT DO NOTHING`).
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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailTag(Base):
    """Глобальный тег почты (без владельца, ADR-044 §5)."""

    __tablename__ = "mail_tags"
    __table_args__ = (
        UniqueConstraint("name", name="uq_mail_tags_name"),
        CheckConstraint(r"color ~ '^#[0-9A-Fa-f]{6}$'", name="ck_mail_tags_color"),
        CheckConstraint("match_mode IN ('any','all')", name="ck_mail_tags_match_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    color: Mapped[str] = mapped_column(Text, nullable=False)
    match_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'any'"))
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MailTagRule(Base):
    """Правило матчинга тега (ADR-044 §2/§5)."""

    __tablename__ = "mail_tag_rules"
    __table_args__ = (
        CheckConstraint(
            "type IN ('subject_contains','body_contains','sender_contains','sender_exact')",
            name="ck_mail_tag_rules_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_tags.id", ondelete="CASCADE", name="fk_mail_tag_rules_tag_id"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MailMessageTag(Base):
    """Связь письмо↔тег (дедуп применения, ADR-044 §2/§5)."""

    __tablename__ = "mail_message_tags"

    message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mail_messages.id", ondelete="CASCADE", name="fk_mail_message_tags_message_id"),
        primary_key=True,
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mail_tags.id", ondelete="CASCADE", name="fk_mail_message_tags_tag_id"),
        primary_key=True,
    )
