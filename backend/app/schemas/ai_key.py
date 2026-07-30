"""Схемы реестра AI-ключей (04-api.md#ai-keys).

Полный ключ (plaintext) НИКОГДА не присутствует в ответах — только маска
`key_masked`. Request-поле ключа — `key` (по контракту 04-api.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.models.ai_key import (
    AiKeyStatus,
    AiProvider,
    BalanceAlertLevel,
    BalanceSyncStatus,
)


class AiKeyCreateRequest(BaseModel):
    """Тело POST /api/ai-keys (поле ключа — `key`, 04-api.md)."""

    name: str = Field(min_length=1, max_length=64)
    provider: AiProvider
    key: str = Field(min_length=1, max_length=512)
    balance_monitoring_enabled: bool = False
    balance_initial_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    balance_low_threshold_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    billing_admin_key: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _balance_fields(self) -> Self:
        if not self.balance_monitoring_enabled:
            return self
        if self.balance_initial_usd is None:
            raise ValueError("balance_initial_usd required when balance monitoring enabled")
        if not self.billing_admin_key or not self.billing_admin_key.strip():
            raise ValueError("billing_admin_key required when balance monitoring enabled")
        return self


class AiKeyUpdateRequest(BaseModel):
    """Тело PATCH /api/ai-keys/{id} (04-api.md). Все поля опциональны.

    `key` пустой (`""`) или отсутствует = «не менять ключ»; поэтому у него нет
    `min_length` (иначе `""` был бы отклонён). Непустой `key` ≤ 512 символов.
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: AiProvider | None = None
    key: str | None = Field(default=None, max_length=512)
    balance_monitoring_enabled: bool | None = None
    balance_initial_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    balance_low_threshold_usd: Decimal | None = Field(default=None, ge=Decimal("0"))
    billing_admin_key: str | None = Field(default=None, max_length=512)


class AiKeyBalanceResetRequest(BaseModel):
    """Тело POST /api/ai-keys/{id}/balance/reset — новый якорь после пополнения."""

    balance_initial_usd: Decimal = Field(ge=Decimal("0"))


class AiKeyOrderRequest(BaseModel):
    """Тело PATCH /api/ai-keys/order — перестановка внутри провайдер-группы."""

    provider: AiProvider
    ids: list[uuid.UUID]


class AiKeyListItem(BaseModel):
    """Элемент списка GET /api/ai-keys и тело ответа 202 POST / 200 PATCH (04-api.md)."""

    id: uuid.UUID
    name: str
    provider: AiProvider
    key_masked: str
    check_status: AiKeyStatus
    error_message: str | None
    position: int
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Число бэков, использующих ключ (COUNT по backends.ai_key_id, ADR-040) — для
    # свёрнутой секции «Бэки» в detail-view ИИ-ключа («Бэков: N») без доп. запроса.
    backend_count: int
    balance_monitoring_enabled: bool
    balance_initial_usd: Decimal | None = None
    balance_remaining_usd: Decimal | None = None
    balance_low_threshold_usd: Decimal | None = None
    balance_anchor_at: datetime | None = None
    balance_last_sync_at: datetime | None = None
    balance_sync_status: BalanceSyncStatus | None = None
    balance_sync_error: str | None = None
    balance_alert_level: BalanceAlertLevel | None = None


class AiKeyListResponse(BaseModel):
    """Ответ 200 GET /api/ai-keys."""

    items: list[AiKeyListItem]


class AiKeyStatusResponse(BaseModel):
    """Ответ 200 GET /api/ai-keys/{id}/status (лёгкий polling статуса)."""

    id: uuid.UUID
    check_status: AiKeyStatus
    error_message: str | None
    last_checked_at: datetime | None
