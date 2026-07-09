r"""teams.mail_group_id (headless mail integration, 1:1 команда↔группа)

Revision ID: 0018_teams_mail_group_id
Revises: 0017_create_sms_module
Create Date: 2026-07-09

Амендмент ADR-038 (headless-интеграция CRM ↔ mail-агрегатор,
03-data-model.md#таблица-teams). Добавляет колонку `teams.mail_group_id` —
привязку CRM-команды (UUID) к группе mail-агрегатора (`groups.id`, int), 1:1.
`UNIQUE` — одна группа агрегатора привязана максимум к одной CRM-команде.
`NULL` = команда без привязки к почте (валидно). Источник истины владения ящиком
остаётся в агрегаторе (`mail_accounts.group_id`); CRM хранит только соответствие.

`upgrade()` — ADD COLUMN nullable + UNIQUE-констрейнт. Существующие команды
получают `mail_group_id = NULL` (сопоставление — ручное через PATCH /api/teams/{id}).
`downgrade()` — снять констрейнт, затем колонку.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_teams_mail_group_id"
down_revision: str | None = "0017_create_sms_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("mail_group_id", sa.Integer(), nullable=True))
    op.create_unique_constraint("uq_teams_mail_group_id", "teams", ["mail_group_id"])


def downgrade() -> None:
    op.drop_constraint("uq_teams_mail_group_id", "teams", type_="unique")
    op.drop_column("teams", "mail_group_id")
