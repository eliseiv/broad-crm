"""Личный archive/delete + nullable read_at (ADR-071).

Revision ID: 0035_mail_reads_archive
Revises: 0034_ai_keys_balance
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_mail_reads_archive"
down_revision: str | None = "0034_ai_keys_balance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mail_message_reads",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "mail_message_reads",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "mail_message_reads", "read_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "mail_message_reads",
        "read_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.drop_column("mail_message_reads", "deleted_at")
    op.drop_column("mail_message_reads", "archived_at")
