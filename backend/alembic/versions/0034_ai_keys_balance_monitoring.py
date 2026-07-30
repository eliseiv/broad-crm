"""Мониторинг оценочного баланса AI-ключей (ADR-070).

Revision ID: 0034_ai_keys_balance
Revises: 0033_document_nodes_rag_excl
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_ai_keys_balance"
down_revision: str | None = "0033_document_nodes_rag_excl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_keys",
        sa.Column(
            "balance_monitoring_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("ai_keys", sa.Column("balance_initial_usd", sa.Numeric(12, 4), nullable=True))
    op.add_column("ai_keys", sa.Column("balance_remaining_usd", sa.Numeric(12, 4), nullable=True))
    op.add_column(
        "ai_keys",
        sa.Column("balance_low_threshold_usd", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "ai_keys",
        sa.Column("balance_anchor_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ai_keys",
        sa.Column("balance_last_sync_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("ai_keys", sa.Column("balance_sync_status", sa.Text(), nullable=True))
    op.add_column("ai_keys", sa.Column("balance_sync_error", sa.Text(), nullable=True))
    op.add_column("ai_keys", sa.Column("balance_alert_level", sa.Text(), nullable=True))
    op.add_column(
        "ai_keys",
        sa.Column(
            "balance_sync_fail_streak",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column("ai_keys", sa.Column("provider_api_key_id", sa.Text(), nullable=True))
    op.add_column("ai_keys", sa.Column("billing_admin_key_encrypted", sa.LargeBinary(), nullable=True))
    op.create_check_constraint(
        "ck_ai_keys_balance_sync_status",
        "ai_keys",
        "balance_sync_status IS NULL OR balance_sync_status IN ('ok','error','unknown')",
    )
    op.create_check_constraint(
        "ck_ai_keys_balance_alert_level",
        "ai_keys",
        "balance_alert_level IS NULL OR balance_alert_level IN ('normal','low','depleted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ai_keys_balance_alert_level", "ai_keys", type_="check")
    op.drop_constraint("ck_ai_keys_balance_sync_status", "ai_keys", type_="check")
    op.drop_column("ai_keys", "billing_admin_key_encrypted")
    op.drop_column("ai_keys", "provider_api_key_id")
    op.drop_column("ai_keys", "balance_sync_fail_streak")
    op.drop_column("ai_keys", "balance_alert_level")
    op.drop_column("ai_keys", "balance_sync_error")
    op.drop_column("ai_keys", "balance_sync_status")
    op.drop_column("ai_keys", "balance_last_sync_at")
    op.drop_column("ai_keys", "balance_anchor_at")
    op.drop_column("ai_keys", "balance_low_threshold_usd")
    op.drop_column("ai_keys", "balance_remaining_usd")
    op.drop_column("ai_keys", "balance_initial_usd")
    op.drop_column("ai_keys", "balance_monitoring_enabled")
