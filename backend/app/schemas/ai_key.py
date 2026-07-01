"""Схемы реестра AI-ключей (04-api.md#ai-keys).

Полный ключ (plaintext) НИКОГДА не присутствует в ответах — только маска
`key_masked`. Request-поле ключа — `key` (по контракту 04-api.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.ai_key import AiKeyStatus, AiProvider


class AiKeyCreateRequest(BaseModel):
    """Тело POST /api/ai-keys (поле ключа — `key`, 04-api.md)."""

    name: str = Field(min_length=1, max_length=64)
    provider: AiProvider
    key: str = Field(min_length=1, max_length=512)


class AiKeyUpdateRequest(BaseModel):
    """Тело PATCH /api/ai-keys/{id} (04-api.md). Все поля опциональны.

    `key` пустой (`""`) или отсутствует = «не менять ключ»; поэтому у него нет
    `min_length` (иначе `""` был бы отклонён). Непустой `key` ≤ 512 символов.
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: AiProvider | None = None
    key: str | None = Field(default=None, max_length=512)


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


class AiKeyListResponse(BaseModel):
    """Ответ 200 GET /api/ai-keys."""

    items: list[AiKeyListItem]


class AiKeyStatusResponse(BaseModel):
    """Ответ 200 GET /api/ai-keys/{id}/status (лёгкий polling статуса)."""

    id: uuid.UUID
    check_status: AiKeyStatus
    error_message: str | None
    last_checked_at: datetime | None
