"""Схемы реестра пользователей (04-api.md#users, ADR-021, ADR-022).

Пароль (plaintext) НИКОГДА не присутствует в ответах — только на вход. `username`
валидируется сервисом (кириллица-допускающий формат) → 422 unprocessable; поэтому
в схеме — простой `str` (без format-констрейнтов, чтобы не давать преждевременный
400 вместо нормативного 422). `email` (опц.) валидируется сервисом → 422. `team_ids`
(опц., CRM-команды) — существование проверяет сервис → 422. Длина пароля при
создании — schema-level (400).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TeamRef(BaseModel):
    """Ссылка на CRM-команду пользователя (id + название) для списка `teams`."""

    id: uuid.UUID
    name: str


class UserCreateRequest(BaseModel):
    """Тело POST /api/users (04-api.md#post-apiusers).

    `password` 8–128 (schema-level → 400 при нарушении длины). `username`/`email`/
    `role_id`/`team_ids` — существование/формат валидируются сервисом (422).
    """

    username: str
    email: str | None = None
    password: str = Field(min_length=8, max_length=128)
    role_id: uuid.UUID
    team_ids: list[uuid.UUID] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    """Тело PATCH /api/users/{id} (04-api.md#patch-apiusersid). Все поля опц.

    «Переданное поле» определяется по `model_fields_set` (Pydantic v2 `exclude_unset`).
    `username` не редактируется. `email`: не передано → не менять; `null`/`""` → убрать;
    валидный → установить (сервис). `password` без schema-констрейнта длины: пустая
    строка `""` и длина вне 8–128 валидируются сервисом → 422 unprocessable (`""` — не
    «очистка»: у пользователя всегда есть пароль). `team_ids`: передано → полностью
    заменяет набор CRM-команд (лидер своих команд сохраняется — инвариант).
    """

    email: str | None = None
    role_id: uuid.UUID | None = None
    is_active: bool | None = None
    password: str | None = None
    team_ids: list[uuid.UUID] | None = None


class UserListItem(BaseModel):
    """Элемент GET /api/users и тело 201 POST / 200 PATCH (04-api.md#users).

    Пароль (`password`/`password_hash`) в ответах отсутствует всегда. `teams` —
    CRM-команды пользователя (ADR-022), не mail-«команды».
    """

    id: uuid.UUID
    username: str
    email: str | None
    role_id: uuid.UUID
    role_name: str
    is_active: bool
    teams: list[TeamRef]
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """Ответ 200 GET /api/users."""

    items: list[UserListItem]
