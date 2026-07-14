r"""user_channel_teams + users.*_includes_unassigned — per-channel scope (ADR-055 §2.4)

Revision ID: 0027_user_channel_teams
Revises: 0026_users_is_system
Create Date: 2026-07-14

Дополнительные команды по каналам («СМС»/«Почты») сверх базового членства
(03-data-model.md#таблица-user_channel_teams):

- **`user_channel_teams` хранит ТОЛЬКО добавку.** Команда, входящая в `user_teams` того
  же пользователя, сюда не пишется (инвариант нормализации ADR-055 §2.3 — обеспечивают
  сервисы users/teams; БД-триггером он НЕ форсируется, идиома проекта: инвариант держит
  сервис как единственная точка записи).
- **`channel` — `text` + CHECK**, а не PG-enum: каналов два, третий не планируется, а
  enum потребовал бы `ALTER TYPE` (образцы «свободных» CHECK'ов — `ck_mail_tag_rules_type`,
  `ck_notifier_alert_log_kind`).
- **`ix_user_channel_teams_team_id` ОБЯЗАТЕЛЕН** (§2.1): обе FK — `ON DELETE CASCADE`, и
  без индекса каждое `DELETE /api/teams/{id}` давало бы seq-scan; он же обслуживает
  обратную выборку «кому канал даёт эту команду».
- **Флаги `users.*_includes_unassigned`** — «Без команды» канала (§2.2): доступ к
  объектам с `team_id IS NULL`. Булева колонка, а не строка в таблице добавок: `NULL` в
  составе `PRIMARY KEY` PostgreSQL не допускает.
- **Backfill НЕ выполняется и НЕ нужен** (§2.4): при пустых добавках и `false`-флагах
  эффективный scope канала ТОЖДЕСТВЕННО равен `user_teams` — ровно то, что действует
  сегодня ⇒ регрессии видимости у существующих пользователей нет, переносить нечего.

Идентификатор ревизии — `0027_user_channel_teams` (23 символа), укладывается в предел
`alembic_version.version_num varchar(32)` (03-data-model.md#1-revision-id).
`downgrade()` — рабочий: DROP таблицы + DROP обеих колонок (миграция чисто аддитивная).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0027_user_channel_teams"
down_revision: str | None = "0026_users_is_system"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_channel_teams",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "channel", "team_id", name="pk_user_channel_teams"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_channel_teams_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_user_channel_teams_team_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("channel IN ('mail', 'sms')", name="ck_user_channel_teams_channel"),
    )
    op.create_index(
        "ix_user_channel_teams_team_id",
        "user_channel_teams",
        ["team_id"],
    )
    op.add_column(
        "users",
        sa.Column(
            "mail_includes_unassigned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "sms_includes_unassigned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "sms_includes_unassigned")
    op.drop_column("users", "mail_includes_unassigned")
    op.drop_index("ix_user_channel_teams_team_id", table_name="user_channel_teams")
    op.drop_table("user_channel_teams")
