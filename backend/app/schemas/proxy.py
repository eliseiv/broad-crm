"""Схемы реестра прокси (04-api.md#proxies).

Пароль (plaintext, в любом виде) НИКОГДА не присутствует в ответах — вместо него
производный флаг `has_password`. `username` (логин) — не секрет, возвращается как
есть. Request-поле секрета — `password` (по контракту 04-api.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.proxy import ProxyStatus, ProxyType


class ProxyCreateRequest(BaseModel):
    """Тело POST /api/proxies (04-api.md#post-apiproxies)."""

    name: str = Field(min_length=1, max_length=64)
    proxy_type: ProxyType
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    # username/password опциональны: отсутствует/null/"" → без логина/пароля.
    # Нет min_length, иначе "" был бы отклонён (по контракту "" = убрать значение).
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=512)


class ProxyUpdateRequest(BaseModel):
    """Тело PATCH /api/proxies/{id} (04-api.md#patch-apiproxiesid). Все поля опц.

    «Переданное поле» определяется по `model_fields_set` — это позволяет отличить
    «поле отсутствует» от «поле передано пустым» (`null`/`""`). `username`/`password`
    без `min_length`: `""` допустимо и означает «убрать значение».
    """

    name: str | None = Field(default=None, min_length=1, max_length=64)
    proxy_type: ProxyType | None = None
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=512)


class ProxyOrderRequest(BaseModel):
    """Тело PATCH /api/proxies/order — перестановка единого списка (04-api.md)."""

    ids: list[uuid.UUID]


class ProxyListItem(BaseModel):
    """Элемент GET /api/proxies и тело 202 POST / 200 PATCH (04-api.md).

    Пароль не раскрывается ни фрагментами, ни маской — только `has_password`.
    """

    id: uuid.UUID
    name: str
    proxy_type: ProxyType
    host: str
    port: int
    username: str | None
    has_password: bool
    check_status: ProxyStatus
    error_message: str | None
    position: int
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProxyListResponse(BaseModel):
    """Ответ 200 GET /api/proxies."""

    items: list[ProxyListItem]


class ProxyStatusResponse(BaseModel):
    """Ответ 200 GET /api/proxies/{id}/status (лёгкий polling статуса)."""

    id: uuid.UUID
    check_status: ProxyStatus
    error_message: str | None
    last_checked_at: datetime | None
