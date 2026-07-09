"""Схемы реестра бэков (04-api.md#backends).

`code` уникален по реестру (дубль → `409 backend_code_taken`). `domain`
канонизируется на входе (`POST`/`PATCH`) к форме `https://<host>/` (ADR-042) —
нормализация выполняется в сервисе. Секреты `api_key`/`admin_api_key` (ADR-040)
шифруются Fernet at-rest и НИКОГДА не отдаются в общих схемах — только флаги
`has_api_key`/`has_admin_api_key` (по образцу `has_password` прокси); полные
значения — только on-demand reveal под `backends:edit`. `git`/`note` — НЕ секреты,
отдаются как есть.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.service_backend import BackendStatus


class BackendCreateRequest(BaseModel):
    """Тело POST /api/backends (04-api.md#post-apibackends).

    Опциональные `server_id`/`ai_key_id` (FK), `api_key`/`admin_api_key` (секреты),
    `git`/`note` (не секреты) — ADR-040. Секреты/`git`/`note` без `min_length`:
    `null`/`""`/отсутствует = не задавать.
    """

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    domain: str = Field(min_length=1, max_length=255)
    server_id: uuid.UUID | None = None
    ai_key_id: uuid.UUID | None = None
    api_key: str | None = Field(default=None, max_length=512)
    admin_api_key: str | None = Field(default=None, max_length=512)
    git: str | None = Field(default=None, max_length=2048)
    note: str | None = Field(default=None, max_length=4096)


class BackendUpdateRequest(BaseModel):
    """Тело PATCH /api/backends/{id} (04-api.md#patch-apibackendsid). Все поля опц.

    «Переданное поле» определяется по `model_fields_set` — это позволяет отличить
    «поле отсутствует» от «поле передано» (Pydantic v2 `exclude_unset`). Для FK/секретов/
    `git`/`note` presence-семантика: отсутствует → не менять; `null`/`""` → обнулить/
    очистить; непустое значение → установить (ADR-040). Секреты/`git`/`note` без
    `min_length` (иначе `""` был бы отклонён; `""` = очистить).
    """

    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    domain: str | None = Field(default=None, min_length=1, max_length=255)
    server_id: uuid.UUID | None = None
    ai_key_id: uuid.UUID | None = None
    api_key: str | None = Field(default=None, max_length=512)
    admin_api_key: str | None = Field(default=None, max_length=512)
    git: str | None = Field(default=None, max_length=2048)
    note: str | None = Field(default=None, max_length=4096)


class BackendOrderRequest(BaseModel):
    """Тело PATCH /api/backends/order — перестановка единого списка (04-api.md)."""

    ids: list[uuid.UUID]


class BackendListItem(BaseModel):
    """Элемент GET /api/backends и тело 202 POST / 200 PATCH (04-api.md#backends).

    Секреты `api_key`/`admin_api_key` НЕ отдаются — только `has_api_key`/
    `has_admin_api_key` (ADR-040). `server_name`/`ai_key_name` — join имён связанных
    сущностей для отображения (`null`, если связи нет). `git`/`note` — не секреты.
    """

    id: uuid.UUID
    code: str
    name: str
    domain: str
    server_id: uuid.UUID | None
    server_name: str | None
    ai_key_id: uuid.UUID | None
    ai_key_name: str | None
    has_api_key: bool
    has_admin_api_key: bool
    git: str | None
    note: str | None
    check_status: BackendStatus
    error_message: str | None
    position: int
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BackendListResponse(BaseModel):
    """Ответ 200 GET /api/backends."""

    items: list[BackendListItem]


class BackendRef(BaseModel):
    """Компактная ссылка на бэк для reverse-lookup «бэки сервера»/«бэки ключа» (ADR-040).

    Только идентификация (`code`/`name`/`domain`) — секреты/связи не отдаются
    (04-api.md#схема-backendref-reverse-lookup).
    """

    code: str
    name: str
    domain: str


class BackendRefListResponse(BaseModel):
    """Ответ 200 GET /api/servers/{id}/backends и /api/ai-keys/{id}/backends (ADR-040)."""

    backends: list[BackendRef]


class BackendStatusResponse(BaseModel):
    """Ответ 200 GET /api/backends/{id}/status (лёгкий polling статуса)."""

    id: uuid.UUID
    check_status: BackendStatus
    error_message: str | None
    last_checked_at: datetime | None
