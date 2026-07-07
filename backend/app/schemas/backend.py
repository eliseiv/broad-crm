"""Схемы реестра бэков (04-api.md#backends).

Секрета у сущности нет — все поля (`code`/`name`/`domain`) публичны и
возвращаются в API как есть. `code` уникален по реестру (дубль →
`409 backend_code_taken`). `domain` нормализуется на входе (`POST`/`PATCH`) и
хранится «голым» (`host[:port]`, без схемы/пути) — нормализация выполняется в
сервисе (04-api.md#backends, modules/backends).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.service_backend import BackendStatus


class BackendCreateRequest(BaseModel):
    """Тело POST /api/backends (04-api.md#post-apibackends)."""

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    domain: str = Field(min_length=1, max_length=255)


class BackendUpdateRequest(BaseModel):
    """Тело PATCH /api/backends/{id} (04-api.md#patch-apibackendsid). Все поля опц.

    «Переданное поле» определяется по `model_fields_set` — это позволяет отличить
    «поле отсутствует» от «поле передано» (Pydantic v2 `exclude_unset`).
    """

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    domain: str | None = Field(default=None, min_length=1, max_length=255)


class BackendOrderRequest(BaseModel):
    """Тело PATCH /api/backends/order — перестановка единого списка (04-api.md)."""

    ids: list[uuid.UUID]


class BackendListItem(BaseModel):
    """Элемент GET /api/backends и тело 202 POST / 200 PATCH (04-api.md#backends)."""

    id: uuid.UUID
    code: str
    name: str
    domain: str
    check_status: BackendStatus
    error_message: str | None
    position: int
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BackendListResponse(BaseModel):
    """Ответ 200 GET /api/backends."""

    items: list[BackendListItem]


class BackendStatusResponse(BaseModel):
    """Ответ 200 GET /api/backends/{id}/status (лёгкий polling статуса)."""

    id: uuid.UUID
    check_status: BackendStatus
    error_message: str | None
    last_checked_at: datetime | None
