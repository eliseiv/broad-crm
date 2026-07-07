"""create proxies table

Revision ID: 0006_create_proxies
Revises: 0005_create_notifier_alert_log
Create Date: 2026-07-07

Реестр прокси (HTTP/HTTPS/SOCKS5) с автоматической проверкой доступности
(ADR-019, 03-data-model.md#таблица-proxies). UUID PK gen_random_uuid, CHECK на
proxy_type/port/check_status/длины name/host; секрет `password_encrypted bytea`
NULL (опц., Fernet); колонка `position` (единый список, drag-and-drop) + индекс
`ix_proxies_position`. Таблица создаётся ПУСТОЙ — backfill не выполняется.
`downgrade()` = DROP TABLE (индекс снимается вместе с таблицей;
07-deployment.md#откат-миграций-бд).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_create_proxies"
down_revision: str | None = "0005_create_notifier_alert_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proxies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("proxy_type", sa.Text(), nullable=False),
        sa.Column("host", sa.Text(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("password_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column(
            "check_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_proxies"),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64",
            name="ck_proxies_name_len",
        ),
        sa.CheckConstraint(
            "proxy_type IN ('http','https','socks5')",
            name="ck_proxies_proxy_type",
        ),
        sa.CheckConstraint(
            "char_length(host) BETWEEN 1 AND 255",
            name="ck_proxies_host_len",
        ),
        sa.CheckConstraint("port BETWEEN 1 AND 65535", name="ck_proxies_port"),
        sa.CheckConstraint(
            "check_status IN ('pending','working','error')",
            name="ck_proxies_check_status",
        ),
    )
    op.create_index("ix_proxies_position", "proxies", ["position"])


def downgrade() -> None:
    op.drop_table("proxies")
