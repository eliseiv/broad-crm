"""create servers table

Revision ID: 0001_create_servers
Revises:
Create Date: 2026-06-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_create_servers"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("ssh_user", sa.Text(), nullable=False),
        sa.Column("ssh_password_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "exporter_port",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("9100"),
        ),
        sa.Column(
            "provision_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="ck_servers_name_len"),
        sa.CheckConstraint(
            "char_length(ssh_user) BETWEEN 1 AND 64", name="ck_servers_ssh_user_len"
        ),
        sa.CheckConstraint("exporter_port BETWEEN 1 AND 65535", name="ck_servers_exporter_port"),
        sa.CheckConstraint(
            "provision_status IN ('pending','installing','online','error')",
            name="ck_servers_provision_status",
        ),
        sa.UniqueConstraint("ip", name="uq_servers_ip"),
    )

    op.create_index("ix_servers_provision_status", "servers", ["provision_status"], unique=False)
    op.create_index(
        "ix_servers_created_at",
        "servers",
        [sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_servers_created_at", table_name="servers")
    op.drop_index("ix_servers_provision_status", table_name="servers")
    op.drop_table("servers")
