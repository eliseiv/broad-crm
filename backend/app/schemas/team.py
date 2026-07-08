"""Схемы реестра CRM-команд (04-api.md#teams, ADR-022).

CRM-команды ≠ mail-«команды» (`MailTeam`, integer, прокси без хранения). Здесь —
uuid, лидер + участники, БД CRM. `name` — кириллица-допускающий формат (как username),
валидируется сервисом → 422 unprocessable. Существование `leader_id`/`member_ids`
проверяет сервис → 422 (`details[].field`). Инвариант «лидер ∈ участники» — сервис.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TeamMember(BaseModel):
    """Участник команды (id + логин) для списка `members`."""

    id: uuid.UUID
    username: str


class TeamCreateRequest(BaseModel):
    """Тело POST /api/teams (04-api.md#post-apiteams).

    `member_ids` опц. (default `[]`); лидер добавляется в участники автоматически.
    `name`/существование ссылок валидируются сервисом (422).
    """

    name: str
    leader_id: uuid.UUID
    member_ids: list[uuid.UUID] = Field(default_factory=list)


class TeamUpdateRequest(BaseModel):
    """Тело PATCH /api/teams/{id} (04-api.md#patch-apiteamsid). Все поля опц.

    «Переданное поле» — по `model_fields_set` (Pydantic v2 `exclude_unset`). `member_ids`,
    если передано, ПОЛНОСТЬЮ заменяет состав (лидер всегда включается в итоговый набор).
    """

    name: str | None = None
    leader_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] | None = None


class TeamListItem(BaseModel):
    """Элемент GET /api/teams и тело 201 POST / 200 PATCH (04-api.md#teams)."""

    id: uuid.UUID
    name: str
    leader_id: uuid.UUID
    leader_username: str
    member_count: int
    members: list[TeamMember]
    created_at: datetime
    updated_at: datetime


class TeamListResponse(BaseModel):
    """Ответ 200 GET /api/teams."""

    items: list[TeamListItem]
