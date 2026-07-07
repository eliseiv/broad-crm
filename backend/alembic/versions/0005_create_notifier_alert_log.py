"""create notifier_alert_log table

Revision ID: 0005_create_notifier_alert_log
Revises: 0004_create_notifier_state
Create Date: 2026-07-07

Append-only durable-лог отправленных серверных алертов Telegram-нотификатора
(ADR-018, 03-data-model.md#таблица-notifier_alert_log). `bigint identity` PK,
`server_id` NULL с FK → servers ON DELETE SET NULL (лог переживает удаление
сервера), CHECK на kind, индекс по created_at DESC под ретенцию/просмотр.
Таблица создаётся ПУСТОЙ — backfill не выполняется. `downgrade()` = DROP TABLE
(индекс снимается вместе с таблицей; 07-deployment.md#откат-миграций-бд).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_create_notifier_alert_log"
down_revision: str | None = "0004_create_notifier_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifier_alert_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("server_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name="fk_notifier_alert_log_server_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifier_alert_log"),
        sa.CheckConstraint(
            "kind IN ('offline','recovered','warning','critical')",
            name="ck_notifier_alert_log_kind",
        ),
    )
    op.create_index(
        "ix_notifier_alert_log_created_at",
        "notifier_alert_log",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_table("notifier_alert_log")
