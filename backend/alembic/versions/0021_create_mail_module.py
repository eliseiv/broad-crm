r"""create mail module tables (ADR-044 §2 — полный перенос модуля «Почты» в CRM)

Revision ID: 0021_create_mail_module
Revises: 0020_backends_domain_canon
Create Date: 2026-07-10

Новые таблицы (ADR-044 §2): `mail_accounts` (каталог; `id` = int из агрегатора),
`mail_messages` (BIGSERIAL, UNIQUE(mail_account_id, uidvalidity, uid), компаундная
лента по `(internal_date DESC, id DESC)`), `mail_tags`/`mail_tag_rules`/
`mail_message_tags` (глобальный каталог), `mail_telegram_links` (chat_id PK,
user_id NULLABLE), `mail_telegram_notifications` (дедуп доставки), `mail_user_settings`.

**Не** создаёт `mail_forwarding`/`mail_message_forwards`/`mail_sent_messages`
(forwarding отложен TD-040, reply/sent — S3). `teams.mail_group_id` **не трогается**
(его drop — отдельной миграцией после ETL, ADR-044 §2). Таблицы стартуют пустыми (данные мигрируются
отдельным cut-over-скриптом, §10). `downgrade()` — DROP в обратном FK-порядке.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_create_mail_module"
down_revision: str | None = "0020_backends_domain_canon"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- mail_accounts (каталог; id = id ящика в агрегаторе, не autoincrement) ---
    op.create_table(
        "mail_accounts",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("down_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_mail_accounts"),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_mail_accounts_team_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_mail_accounts_team_id", "mail_accounts", ["team_id"])

    # --- mail_messages (system of record; BIGSERIAL id) ---
    op.create_table(
        "mail_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("mail_account_id", sa.Integer(), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("message_id_header", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("from_addr", sa.Text(), nullable=False),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("to_addrs", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("cc_addrs", sa.Text(), nullable=True),
        sa.Column("internal_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("body_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("body_present", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("in_reply_to", sa.Text(), nullable=True),
        sa.Column("refs_header", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mail_messages"),
        sa.ForeignKeyConstraint(
            ["mail_account_id"],
            ["mail_accounts.id"],
            name="fk_mail_messages_account_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "mail_account_id",
            "uidvalidity",
            "uid",
            name="uq_mail_messages_account_uidv_uid",
        ),
    )
    # Лента по ящику: (mail_account_id, internal_date DESC, id DESC).
    op.create_index(
        "ix_mail_messages_account_feed",
        "mail_messages",
        ["mail_account_id", sa.text("internal_date DESC"), sa.text("id DESC")],
    )
    # Глобальная лента admin-scope: (internal_date DESC, id DESC).
    op.create_index(
        "ix_mail_messages_feed",
        "mail_messages",
        [sa.text("internal_date DESC"), sa.text("id DESC")],
    )
    # Очередь диспетчера (S4): partial по notified_at IS NULL.
    op.create_index(
        "ix_mail_messages_notify",
        "mail_messages",
        ["id"],
        postgresql_where=sa.text("notified_at IS NULL"),
    )

    # --- mail_tags (глобальный каталог) ---
    op.create_table(
        "mail_tags",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=False),
        sa.Column("match_mode", sa.Text(), nullable=False, server_default=sa.text("'any'")),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
        sa.PrimaryKeyConstraint("id", name="pk_mail_tags"),
        sa.UniqueConstraint("name", name="uq_mail_tags_name"),
        sa.CheckConstraint(r"color ~ '^#[0-9A-Fa-f]{6}$'", name="ck_mail_tags_color"),
        sa.CheckConstraint("match_mode IN ('any','all')", name="ck_mail_tags_match_mode"),
    )

    # --- mail_tag_rules ---
    op.create_table(
        "mail_tag_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mail_tag_rules"),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["mail_tags.id"],
            name="fk_mail_tag_rules_tag_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "type IN ('subject_contains','body_contains','sender_contains','sender_exact')",
            name="ck_mail_tag_rules_type",
        ),
    )

    # --- mail_message_tags ---
    op.create_table(
        "mail_message_tags",
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("message_id", "tag_id", name="pk_mail_message_tags"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["mail_messages.id"],
            name="fk_mail_message_tags_message_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["mail_tags.id"],
            name="fk_mail_message_tags_tag_id",
            ondelete="CASCADE",
        ),
    )

    # --- mail_telegram_links (chat_id PK; user_id NULLABLE) ---
    op.create_table(
        "mail_telegram_links",
        sa.Column("telegram_user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("telegram_user_id", name="pk_mail_telegram_links"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_mail_telegram_links_user_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_mail_tg_links_user_id",
        "mail_telegram_links",
        ["user_id"],
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_mail_tg_links_username",
        "mail_telegram_links",
        ["username"],
        postgresql_where=sa.text("user_id IS NULL"),
    )

    # --- mail_telegram_notifications (дедуп доставки + история) ---
    op.create_table(
        "mail_telegram_notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_mail_telegram_notifications"),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["mail_messages.id"],
            name="fk_mail_tg_notif_message_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("message_id", "telegram_user_id", name="uq_mail_tg_notif_msg_chat"),
        sa.CheckConstraint(
            "status IN ('pending','sent','failed','dead')",
            name="ck_mail_tg_notif_status",
        ),
    )

    # --- mail_user_settings (opt-out) ---
    op.create_table(
        "mail_user_settings",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tg_notifications_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_mail_user_settings"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_mail_user_settings_user_id",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("mail_user_settings")

    op.drop_table("mail_telegram_notifications")

    op.drop_index("ix_mail_tg_links_username", table_name="mail_telegram_links")
    op.drop_index("ix_mail_tg_links_user_id", table_name="mail_telegram_links")
    op.drop_table("mail_telegram_links")

    op.drop_table("mail_message_tags")

    op.drop_table("mail_tag_rules")

    op.drop_table("mail_tags")

    op.drop_index("ix_mail_messages_notify", table_name="mail_messages")
    op.drop_index("ix_mail_messages_feed", table_name="mail_messages")
    op.drop_index("ix_mail_messages_account_feed", table_name="mail_messages")
    op.drop_table("mail_messages")

    op.drop_index("ix_mail_accounts_team_id", table_name="mail_accounts")
    op.drop_table("mail_accounts")
