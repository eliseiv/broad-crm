r"""Модель таблицы `backends` (03-data-model.md#таблица-backends, modules/backends).

Устроена по образцу `proxies` (модель со статусом + фоновый монитор). Идентификатор
`code` УНИКАЛЕН (`409 backend_code_taken` при дубле); проверка — прямой
`GET {domain}health`; список единый (без группировки). Домен хранится в каноне
`https://<host>/` — инвариант CHECK `domain ~ '^https://[^\s/]+/$'` (ADR-042).
Связи `server_id`/`ai_key_id` (FK, ON DELETE SET NULL) и секреты
`api_key_encrypted`/`admin_api_key_encrypted` (Fernet at-rest) + `git`/`note`
(не секреты) — ADR-040.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BackendStatus(str, enum.Enum):
    """Конечный автомат статуса проверки бэка (03-data-model.md, ADR-020).

    Состояние переживает рестарт (источник переходов — БД). У бэков НЕТ исхода
    `unknown`: недоступность бэка и есть отслеживаемое событие.
    """

    pending = "pending"
    working = "working"
    error = "error"


class Backend(Base):
    """Реестр бэков. Секрета нет — все поля публичны (ADR-020). `code` уникален."""

    __tablename__ = "backends"
    __table_args__ = (
        CheckConstraint("char_length(code) BETWEEN 1 AND 64", name="ck_backends_code_len"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="ck_backends_name_len"),
        CheckConstraint(
            r"char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^https://[^\s/]+/$'",
            name="ck_backends_domain",
        ),
        CheckConstraint(
            "check_status IN ('pending','working','error')",
            name="ck_backends_check_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    check_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Связи и секреты бэка (ADR-040, миграция 0019). FK — ON DELETE SET NULL:
    # удаление сервера/ключа обнуляет связь, но не удаляет бэк. Секреты
    # api_key/admin_api_key — Fernet at-rest (bytea), в API только has_* флаги.
    server_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("servers.id", ondelete="SET NULL"),
        nullable=True,
    )
    ai_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_keys.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    admin_api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # git/note — НЕ секреты (plaintext, отдаются в обычных ответах).
    git: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Grace-порог алерта недоступности (ADR-024, миграция 0013). `error_since` — начало
    # текущего непрерывного эпизода недоступности; `alert_sent` — отправлен ли 🔴 для него.
    error_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
