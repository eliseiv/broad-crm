"""Схемы реестра пользователей (04-api.md#users, ADR-021/022/025).

Пароль (plaintext) НИКОГДА не присутствует в ответах — только на вход. `username`
валидируется сервисом (кириллица-допускающий формат) → 422 unprocessable; поэтому
в схеме — простой `str` (без format-констрейнтов, чтобы не давать преждевременный
400 вместо нормативного 422). `telegram` (опц., ADR-025; заменяет прежний `email`)
валидируется сервисом → 422. `team_ids` (опц., CRM-команды) — существование проверяет
сервис → 422. `password` **опционален** (беспарольный пользователь, ADR-025); длина
8–128 при наличии валидируется сервисом → 422.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TeamRef(BaseModel):
    """Ссылка на CRM-команду пользователя (id + название) для списка `teams`."""

    id: uuid.UUID
    name: str


class UserCreateRequest(BaseModel):
    """Тело POST /api/users (04-api.md#post-apiusers).

    `password` **опционален** (беспарольный пользователь, ADR-025); длина 8–128 при
    наличии валидируется сервисом (422). `username`/`telegram`/`role_id`/`team_ids` —
    существование/формат валидируются сервисом (422).
    """

    username: str
    telegram: str | None = None
    password: str | None = None
    role_id: uuid.UUID
    team_ids: list[uuid.UUID] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    """Тело PATCH /api/users/{id} (04-api.md#patch-apiusersid). Все поля опц.

    «Переданное поле» определяется по `model_fields_set` (Pydantic v2 `exclude_unset`).
    `username` не редактируется. `telegram`: не передано → не менять; `null`/`""` →
    убрать; валидный → установить (сервис). `password` без schema-констрейнта длины:
    пустая строка `""` и длина вне 8–128 валидируются сервисом → 422 unprocessable
    (`""` — не «очистка»). `team_ids`: передано → полностью заменяет набор CRM-команд;
    при исключении из команды, которую пользователь ведёт, лидерство авто-передаётся
    (ADR-026).
    """

    telegram: str | None = None
    role_id: uuid.UUID | None = None
    is_active: bool | None = None
    password: str | None = None
    team_ids: list[uuid.UUID] | None = None


class UserListItem(BaseModel):
    """Элемент GET /api/users и тело 201 POST / 200 PATCH (04-api.md#users).

    Пароль (`password`/`password_hash`) в ответах отсутствует всегда — только
    производный `has_password`. `status` — производный тристатус (ADR-028): `is_active`
    приоритетен (`false` → `"inactive"`), затем факт первого входа (`first_login_at`);
    сама метка `first_login_at` наружу не отдаётся. `teams` — CRM-команды пользователя
    (ADR-022), не mail-«команды».
    """

    id: uuid.UUID
    username: str
    telegram: str | None
    has_password: bool
    role_id: uuid.UUID
    role_name: str
    is_active: bool
    status: Literal["pending", "active", "inactive"]
    teams: list[TeamRef]
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """Ответ 200 GET /api/users."""

    items: list[UserListItem]
