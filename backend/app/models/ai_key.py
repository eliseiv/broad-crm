"""Модель таблицы `ai_keys` (03-data-model.md, modules/ai-keys)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AiProvider(str, enum.Enum):
    """Поддерживаемые AI-провайдеры (03-data-model.md, расширяется добавлением в enum)."""

    openai = "openai"
    anthropic = "anthropic"


class BalanceSyncStatus(str, enum.Enum):
    """Исход последней синхронизации баланса (ADR-070)."""

    ok = "ok"
    error = "error"
    unknown = "unknown"


class BalanceAlertLevel(str, enum.Enum):
    """Уровень алерта по остатку (отдельно от check_status)."""

    normal = "normal"
    low = "low"
    depleted = "depleted"


class CreditStatus(str, enum.Enum):
    """Бинарный исход credit-probe (ADR-075): есть кредиты / нет."""

    ok = "ok"
    depleted = "depleted"


class AiKeyStatus(str, enum.Enum):
    """Конечный автомат статуса проверки ключа (03-data-model.md).

    Состояние переживает рестарт (источник переходов — БД, ADR-010).
    """

    pending = "pending"
    working = "working"
    error = "error"


class AiKey(Base):
    """Реестр AI-ключей. Полный ключ — только `key_encrypted` (Fernet, ADR-010)."""

    __tablename__ = "ai_keys"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="ck_ai_keys_name_len"),
        CheckConstraint("provider IN ('openai','anthropic')", name="ck_ai_keys_provider"),
        CheckConstraint(
            "check_status IN ('pending','working','error')",
            name="ck_ai_keys_check_status",
        ),
        CheckConstraint(
            "balance_sync_status IS NULL OR balance_sync_status IN ('ok','error','unknown')",
            name="ck_ai_keys_balance_sync_status",
        ),
        CheckConstraint(
            "balance_alert_level IS NULL OR balance_alert_level IN ('normal','low','depleted')",
            name="ck_ai_keys_balance_alert_level",
        ),
        CheckConstraint(
            "credit_status IS NULL OR credit_status IN ('ok','depleted')",
            name="ck_ai_keys_credit_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_prefix: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_last4: Mapped[str | None] = mapped_column(Text, nullable=True)
    check_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    balance_monitoring_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    balance_initial_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    balance_remaining_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    balance_low_threshold_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    balance_anchor_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    balance_last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    balance_sync_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance_alert_level: Mapped[str | None] = mapped_column(Text, nullable=True)
    balance_sync_fail_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    provider_api_key_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    billing_admin_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    credit_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    credit_last_probed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    credit_probe_error: Mapped[str | None] = mapped_column(Text, nullable=True)
