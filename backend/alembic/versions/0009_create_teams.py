r"""create teams and user_teams tables (CRM-команды)

Revision ID: 0009_create_teams
Revises: 0008_create_users_roles
Create Date: 2026-07-08

CRM-команды — группировка пользователей вокруг лидера (ADR-022,
03-data-model.md#таблицы-teams-и-user_teams-crm-команды). `teams.name` — «свободный»
DB-CHECK (длина 1–64, без ведущих/хвостовых пробелов, без control-символов); полное
правило (кириллица) — на Pydantic. FK `teams.leader_id → users.id` ON DELETE RESTRICT
(лидера нельзя удалить, не разобравшись с командой → 409 user_is_team_leader).
`user_teams` — первая M2M-таблица в проекте (составной PK, обе FK ON DELETE CASCADE).
Инвариант «лидер ∈ участники» — на сервисе (БД не форсирует). Backfill нет (пусто).
`downgrade()` = DROP user_teams, teams (порядок из-за FK).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_create_teams"
down_revision: str | None = "0008_create_users_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("leader_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        sa.UniqueConstraint("name", name="uq_teams_name"),
        sa.ForeignKeyConstraint(
            ["leader_id"],
            ["users.id"],
            name="fk_teams_leader_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64 "
            "AND name = btrim(name) "
            "AND name !~ '[[:cntrl:]]'",
            name="ck_teams_name",
        ),
    )
    op.create_index("ix_teams_leader_id", "teams", ["leader_id"])

    op.create_table(
        "user_teams",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "team_id", name="pk_user_teams"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_teams_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_user_teams_team_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_user_teams_team_id", "user_teams", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_user_teams_team_id", table_name="user_teams")
    op.drop_table("user_teams")
    op.drop_index("ix_teams_leader_id", table_name="teams")
    op.drop_table("teams")
