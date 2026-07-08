r"""teams: optional leader + auto-transfer support (ADR-026)

Revision ID: 0012_teams_optional_leader
Revises: 0011_user_passwordless_telegram
Create Date: 2026-07-08

Команды без лидера (ADR-026, 03-data-model.md#миграция-0012_teams_optional_leader-концепт):
  1. `teams.leader_id` → nullable + FK пересоздаётся `ON DELETE SET NULL` (было
     `RESTRICT`): удаление пользователя-лидера больше не блокируется командой
     (предохранитель уровня БД; прикладная авто-передача — в сервисе).
  2. `user_teams += created_at timestamptz NOT NULL DEFAULT now()` — «дата добавления»
     участника для детерминированной авто-передачи/авто-назначения лидерства.
Имя FK-констрейнта `fk_teams_leader_id` — фактическое из миграции `0009_create_teams`.
На проде команд 0 → backfill `created_at` через DEFAULT безопасен. `downgrade()`
обратим (лидер снова обязателен, FK `RESTRICT`, снять `created_at`); требует
отсутствия `NULL`-лидеров (на проде команд 0 — не блокирует).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_teams_optional_leader"
down_revision: str | None = "0011_user_passwordless_telegram"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_TEAMS_LEADER = "fk_teams_leader_id"


def upgrade() -> None:
    # 1) leader_id → nullable + FK ON DELETE SET NULL (пересоздать констрейнт).
    op.alter_column(
        "teams",
        "leader_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_constraint(_FK_TEAMS_LEADER, "teams", type_="foreignkey")
    op.create_foreign_key(
        _FK_TEAMS_LEADER,
        "teams",
        "users",
        ["leader_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 2) Дата добавления участника (для авто-передачи лидерства).
    op.add_column(
        "user_teams",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_teams", "created_at")
    op.drop_constraint(_FK_TEAMS_LEADER, "teams", type_="foreignkey")
    op.create_foreign_key(
        _FK_TEAMS_LEADER,
        "teams",
        "users",
        ["leader_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # Требует отсутствия NULL-лидеров (на проде команд 0 — не блокирует).
    op.alter_column(
        "teams",
        "leader_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
