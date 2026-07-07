"""Схемы реестра ролей (04-api.md#roles, ADR-021).

`name` — кириллица-допускающий формат (как username), валидируется сервисом →
422 unprocessable. `permissions` — `{page: [action, ...]}`, валидируется против
каталога сервисом → 422 unprocessable (`details[].field="permissions"`).
Схема оставляет `permissions` структурным `dict[str, list[str]]` (форму проверяет
Pydantic → 400; каноничность против каталога — сервис → 422).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class RoleCreateRequest(BaseModel):
    """Тело POST /api/roles (04-api.md#post-apiroles)."""

    name: str
    permissions: dict[str, list[str]]


class RoleUpdateRequest(BaseModel):
    """Тело PATCH /api/roles/{id} (04-api.md#patch-apirolesid). Все поля опц.

    «Переданное поле» определяется по `model_fields_set` (Pydantic v2 `exclude_unset`):
    `permissions`, если передано, ПОЛНОСТЬЮ заменяет матрицу прав.
    """

    name: str | None = None
    permissions: dict[str, list[str]] | None = None


class RoleListItem(BaseModel):
    """Элемент GET /api/roles и тело 201 POST / 200 PATCH (04-api.md#roles)."""

    id: uuid.UUID
    name: str
    permissions: dict[str, list[str]]
    created_at: datetime
    updated_at: datetime


class RoleListResponse(BaseModel):
    """Ответ 200 GET /api/roles."""

    items: list[RoleListItem]
