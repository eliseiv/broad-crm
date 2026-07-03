"""create notifier_server_state table

Revision ID: 0004_create_notifier_state
Revises: 0003_add_position
Create Date: 2026-07-04

Персист состояния Telegram-нотификатора (ADR-014, 03-data-model.md#таблица-notifier_server_state).
Таблица создаётся ПУСТОЙ — backfill намеренно НЕ выполняется: первый после-деплойный
опрос трактует каждый сервер против здоровой базы (green/online) и шлёт ровно один
catch-up-алерт для сейчас-повышенных/offline серверов (alert-on-first-elevated).
`downgrade()` = DROP TABLE (07-deployment.md#откат-миграций-бд).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_create_notifier_state"
down_revision: str | None = "0003_add_position"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifier_server_state",
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column("zone_cpu", sa.Text(), nullable=True),
        sa.Column("zone_ram", sa.Text(), nullable=True),
        sa.Column("zone_ssd", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name="fk_notifier_server_state_server_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("server_id", name="pk_notifier_server_state"),
        sa.CheckConstraint(
            "zone_cpu IN ('green','yellow','red')",
            name="ck_notifier_server_state_zone_cpu",
        ),
        sa.CheckConstraint(
            "zone_ram IN ('green','yellow','red')",
            name="ck_notifier_server_state_zone_ram",
        ),
        sa.CheckConstraint(
            "zone_ssd IN ('green','yellow','red')",
            name="ck_notifier_server_state_zone_ssd",
        ),
    )


def downgrade() -> None:
    op.drop_table("notifier_server_state")
