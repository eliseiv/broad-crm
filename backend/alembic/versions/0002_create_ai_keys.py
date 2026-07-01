"""create ai_keys table

Revision ID: 0002_create_ai_keys
Revises: 0001_create_servers
Create Date: 2026-07-01

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_create_ai_keys"
down_revision: str | None = "0001_create_servers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("key_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=True),
        sa.Column("key_last4", sa.Text(), nullable=True),
        sa.Column(
            "check_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="ck_ai_keys_name_len"),
        sa.CheckConstraint("provider IN ('openai','anthropic')", name="ck_ai_keys_provider"),
        sa.CheckConstraint(
            "check_status IN ('pending','working','error')",
            name="ck_ai_keys_check_status",
        ),
    )

    op.create_index(
        "ix_ai_keys_created_at",
        "ai_keys",
        [sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_keys_created_at", table_name="ai_keys")
    op.drop_table("ai_keys")
