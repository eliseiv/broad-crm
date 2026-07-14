"""Таблица `user_channel_teams` — per-channel добавки команд (ADR-055 §2.1).

Хранит **только добавку**: команды, входящие в базовое членство (`user_teams`) того же
пользователя, сюда НЕ пишутся (инвариант нормализации §2.3 — обеспечивают сервисы
users/teams как единственные точки записи в `user_teams`). Эффективный scope канала =
`user_teams ∪ user_channel_teams[channel]` (03-data-model.md#таблица-user_channel_teams).

`channel` — `text` + CHECK (каналов два, третий не планируется; enum потребовал бы
`ALTER TYPE` в миграции). Обе FK — `ON DELETE CASCADE`: удаление пользователя/команды
снимает добавки автоматически (нормализации не требует). `ix_user_channel_teams_team_id`
обязателен под каскад при удалении команды (иначе seq-scan) и под обратную выборку.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base

user_channel_teams = Table(
    "user_channel_teams",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_channel_teams_user_id"),
        primary_key=True,
    ),
    Column("channel", Text, primary_key=True),
    Column(
        "team_id",
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE", name="fk_user_channel_teams_team_id"),
        primary_key=True,
    ),
    CheckConstraint("channel IN ('mail', 'sms')", name="ck_user_channel_teams_channel"),
    Index("ix_user_channel_teams_team_id", "team_id"),
)
