r"""create backends table

Revision ID: 0007_create_backends
Revises: 0006_create_proxies
Create Date: 2026-07-07

Реестр бэков (backend-сервисов) с автоматической проверкой доступности
`GET https://{domain}/health` (ADR-020, 03-data-model.md#таблица-backends).
UUID PK gen_random_uuid; `code` УНИКАЛЕН (`uq_backends_code`, дубль →
409 backend_code_taken); CHECK на длины code/name, check_status и инвариант
нормализации домена (`domain ~ '^[^\s/]+$'`); секрета/Fernet нет; колонка
`position` (единый список, drag-and-drop) + индекс `ix_backends_position`.
Таблица создаётся ПУСТОЙ — backfill не выполняется. `downgrade()` = DROP TABLE
(индексы снимаются вместе с таблицей; 07-deployment.md#откат-миграций-бд).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_create_backends"
down_revision: str | None = "0006_create_proxies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backends",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_backends"),
        sa.CheckConstraint(
            "char_length(code) BETWEEN 1 AND 64",
            name="ck_backends_code_len",
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64",
            name="ck_backends_name_len",
        ),
        sa.CheckConstraint(
            r"char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^[^\s/]+$'",
            name="ck_backends_domain",
        ),
        sa.CheckConstraint(
            "check_status IN ('pending','working','error')",
            name="ck_backends_check_status",
        ),
    )
    op.create_index("uq_backends_code", "backends", ["code"], unique=True)
    op.create_index("ix_backends_position", "backends", ["position"])


def downgrade() -> None:
    op.drop_table("backends")
