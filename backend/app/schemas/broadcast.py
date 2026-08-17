"""Схемы модуля «Рассылка» (04-api.md#broadcast, ADR-076)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class BroadcastAudienceRole(BaseModel):
    """Роль для чекбоксов аудитории + счётчики запуска ИИ-бота."""

    id: uuid.UUID
    name: str
    started_count: int
    not_started_count: int


class BroadcastAudienceResponse(BaseModel):
    """Ответ 200 GET /api/broadcasts/audience."""

    roles: list[BroadcastAudienceRole]
    all_started_count: int
    all_not_started_count: int


class BroadcastCreateRequest(BaseModel):
    """Тело POST /api/broadcasts. `text`/`all`/`role_ids` валидирует сервис (422)."""

    text: str
    all: bool = False
    role_ids: list[uuid.UUID] = Field(default_factory=list)


class BroadcastSendResponse(BaseModel):
    """Ответ 200 POST /api/broadcasts (частичный успех тоже 200)."""

    sent: int
    failed: int
    skipped_not_started: int
