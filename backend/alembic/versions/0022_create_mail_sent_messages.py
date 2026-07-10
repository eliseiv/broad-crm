r"""create mail_sent_messages (ADR-044 §2/§8 — запись отправленных reply)

Revision ID: 0022_create_mail_sent_messages
Revises: 0021_create_mail_module
Create Date: 2026-07-10

Таблица `mail_sent_messages` (ADR-044 §2): CRM — инициатор reply, SMTP-отправку
делегирует агрегатору, факт отправки пишет сюда. `teams.mail_group_id` **не трогается**
(его drop — отдельной миграцией после ETL, ADR-044 §2). `mail_forwarding`/`mail_message_forwards` не
создаются (forwarding отложен, TD-040). `downgrade()` — DROP таблицы.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_create_mail_sent_messages"
down_revision: str | None = "0021_create_mail_module"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_sent_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("mail_account_id", sa.Integer(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("to_addrs", sa.Text(), nullable=False),
        sa.Column("cc_addrs", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("in_reply_to", sa.Text(), nullable=True),
        sa.Column("refs_header", sa.Text(), nullable=True),
        sa.Column("smtp_message_id", sa.Text(), nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mail_sent_messages"),
        sa.ForeignKeyConstraint(
            ["mail_account_id"],
            ["mail_accounts.id"],
            name="fk_mail_sent_messages_account_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_mail_sent_messages_user_id",
            ondelete="SET NULL",
        ),
    )


def downgrade() -> None:
    op.drop_table("mail_sent_messages")
