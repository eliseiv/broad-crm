"""Модель таблицы `proxies` (03-data-model.md#таблица-proxies, modules/proxies).

Устроена по образцу `ai_keys` (модель со статусом + фоновый монитор), но:
секрет (`password`) опционален (`password_encrypted` может быть NULL); список
единый (без группировки); в API вместо фрагментов пароля — флаг `has_password`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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


class ProxyType(str, enum.Enum):
    """Тип/схема прокси (03-data-model.md, расширяется добавлением в enum)."""

    http = "http"
    https = "https"
    socks5 = "socks5"


class ProxyStatus(str, enum.Enum):
    """Конечный автомат статуса проверки прокси (03-data-model.md, ADR-019).

    Состояние переживает рестарт (источник переходов — БД). У прокси НЕТ исхода
    `unknown`: недоступность прокси и есть отслеживаемое событие.
    """

    pending = "pending"
    working = "working"
    error = "error"


class Proxy(Base):
    """Реестр прокси. Пароль — только `password_encrypted` (Fernet, опц., ADR-019)."""

    __tablename__ = "proxies"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="ck_proxies_name_len"),
        CheckConstraint(
            "proxy_type IN ('http','https','socks5')",
            name="ck_proxies_proxy_type",
        ),
        CheckConstraint("char_length(host) BETWEEN 1 AND 255", name="ck_proxies_host_len"),
        CheckConstraint("port BETWEEN 1 AND 65535", name="ck_proxies_port"),
        CheckConstraint(
            "check_status IN ('pending','working','error')",
            name="ck_proxies_check_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    proxy_type: Mapped[str] = mapped_column(Text, nullable=False)
    host: Mapped[str] = mapped_column(Text, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    check_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Grace-порог алерта недоступности (ADR-027, миграция 0014). `error_since` — начало
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
