"""Hourly credit-probe AI-ключей (ADR-075): миграция 0036.

Revision ID: 0036_ai_keys_credit_probe
Revises: 0035_mail_reads_archive
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_ai_keys_credit_probe"
down_revision: str | None = "0035_mail_reads_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_keys", sa.Column("credit_status", sa.Text(), nullable=True))
    op.add_column(
        "ai_keys",
        sa.Column("credit_last_probed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("ai_keys", sa.Column("credit_probe_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_ai_keys_credit_status",
        "ai_keys",
        "credit_status IS NULL OR credit_status IN ('ok','depleted')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_ai_keys_credit_status", "ai_keys", type_="check")
    op.drop_column("ai_keys", "credit_probe_error")
    op.drop_column("ai_keys", "credit_last_probed_at")
    op.drop_column("ai_keys", "credit_status")
