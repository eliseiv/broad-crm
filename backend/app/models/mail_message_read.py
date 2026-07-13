"""Модель таблицы `mail_message_reads` — ЛИЧНАЯ прочитанность писем (ADR-050 §2.1).

Таблица связи «пользователь × письмо»: **существование строки = «прочитано» ЭТИМ
пользователем**, отсутствие = «не прочитано» (отдельного булева поля нет). Прочитанность
личная — один и тот же `id` письма у разных пользователей даёт разные `is_unread`.

PK `(user_id, message_id)` обслуживает оба горячих пути ленты (батч-лукап `is_unread` и
анти-джойн фильтра `unread=true`). `ix_mail_message_reads_message_id` обязателен: без него
`ON DELETE CASCADE` со стороны `mail_messages` (удаление ящика) шёл бы seq scan'ом.
`read_at` — диагностика; наружу НЕ отдаётся и при повторной пометке НЕ обновляется
(`ON CONFLICT DO NOTHING` — важно первое открытие).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, PrimaryKeyConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MailMessageRead(Base):
    """Отметка «письмо прочитано пользователем» (ADR-050 §2.1)."""

    __tablename__ = "mail_message_reads"
    __table_args__ = (PrimaryKeyConstraint("user_id", "message_id", name="pk_mail_message_reads"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_mail_message_reads_user_id"),
        nullable=False,
    )
    message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mail_messages.id", ondelete="CASCADE", name="fk_mail_message_reads_message_id"),
        nullable=False,
    )
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# Обязателен (ADR-050 §2.1): PK ведёт с `user_id`, поиск по `message_id` его не использует,
# а каскадное удаление писем при удалении ящика без него — seq scan на каждое письмо.
Index("ix_mail_message_reads_message_id", MailMessageRead.message_id)
