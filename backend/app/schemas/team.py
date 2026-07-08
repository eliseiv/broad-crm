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
    """Тело POST /api/teams (04-api.md#post-apiteams, ADR-026).

    `leader_id`/`member_ids` **опциональны** (пустая команда без лидера допустима).
    Заданный лидер добавляется в участники; при отсутствии лидера первый участник
    авто-назначается лидером. `name`/существование ссылок валидируются сервисом (422).
    """

    name: str
    leader_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] = Field(default_factory=list)


class TeamUpdateRequest(BaseModel):
    """Тело PATCH /api/teams/{id} (04-api.md#patch-apiteamsid, ADR-026). Все поля опц.

    «Переданное поле» — по `model_fields_set` (Pydantic v2 `exclude_unset`). `member_ids`,
    если передано, ПОЛНОСТЬЮ заменяет состав. `leader_id`: uuid → сменить лидера (он ∈
    участники); `null` → снять лидера. Если `leader_id` НЕ передан, а текущий лидер
    исключён из нового состава → авто-передача следующему по `user_teams.created_at`.
    """

    name: str | None = None
    leader_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] | None = None


class TeamListItem(BaseModel):
    """Элемент GET /api/teams и тело 201 POST / 200 PATCH (04-api.md#teams, ADR-026).

    `leader_id`/`leader_username` — `null` у команды без лидера; `member_count` может
    быть `0` (пустая команда).
    """

    id: uuid.UUID
    name: str
    leader_id: uuid.UUID | None
    leader_username: str | None
    member_count: int
    members: list[TeamMember]
    created_at: datetime
    updated_at: datetime


class TeamListResponse(BaseModel):
    """Ответ 200 GET /api/teams."""

    items: list[TeamListItem]
