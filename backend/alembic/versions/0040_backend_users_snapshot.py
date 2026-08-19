r"""Postgres-снимок «Юзеров бэков» + агрегат расходов API (ADR-080 §2)

Revision ID: 0040_backend_users_snapshot
Revises: 0039_users_full_name_telegram
Create Date: 2026-08-19

Две таблицы (03-data-model.md#миграция-0040_backend_users_snapshot-концепт-adr-080):

- `backend_user_snapshot_sources` — одна строка на бэк с admin-ключом: метка свежести,
  сбой последнего цикла, снимок `GET {P}/stats`, агрегат `api_costs` и два признака
  полноты расходов (`revenue_backfill_done`, `revenue_supported`).
- `backend_user_snapshots` — зеркало элемента `GET {P}/users` + экономика карточки.

Backfill не выполняется: таблицы стартуют пустыми, первый `refresh_once()` воркера
наполняет их при старте приложения (до этого `snapshot_at = null`).

`revision = "0040_backend_users_snapshot"` — 27 символов ≤ 32.
`downgrade()` — DROP обеих таблиц (снимок — производные данные, источник истины у бэков).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_backend_users_snapshot"
down_revision: str | None = "0039_users_full_name_telegram"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backend_user_snapshot_sources",
        sa.Column("backend_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats_users_total", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("stats_paid_users", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "stats_payments_sum_usd",
            postgresql.DOUBLE_PRECISION(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "api_costs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "revenue_backfill_done",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("revenue_supported", sa.Boolean(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("backend_id", name="pk_backend_user_snapshot_sources"),
        sa.ForeignKeyConstraint(
            ["backend_id"],
            ["backends.id"],
            name="fk_backend_user_snapshot_sources_backend_id",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "backend_user_snapshots",
        sa.Column("backend_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("is_paid", sa.Boolean(), nullable=True),
        sa.Column("payments_count", sa.Integer(), nullable=True),
        sa.Column("renewals_count", sa.Integer(), nullable=True),
        sa.Column("tokens", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column("subscription_active", sa.Boolean(), nullable=True),
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_id", sa.Text(), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("api_cost_usd", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column("api_cost_providers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("revenue_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("backend_id", "user_id", name="pk_backend_user_snapshots"),
        sa.ForeignKeyConstraint(
            ["backend_id"],
            ["backends.id"],
            name="fk_backend_user_snapshots_backend_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index(
        "ix_backend_user_snapshots_registered_at",
        "backend_user_snapshots",
        [sa.text("registered_at DESC")],
    )
    op.create_index(
        "ix_backend_user_snapshots_backend_registered_at",
        "backend_user_snapshots",
        ["backend_id", sa.text("registered_at DESC")],
    )
    op.create_index("ix_backend_user_snapshots_user_id", "backend_user_snapshots", ["user_id"])
    op.create_index(
        "ix_backend_user_snapshots_external_id", "backend_user_snapshots", ["external_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_backend_user_snapshots_external_id", table_name="backend_user_snapshots")
    op.drop_index("ix_backend_user_snapshots_user_id", table_name="backend_user_snapshots")
    op.drop_index(
        "ix_backend_user_snapshots_backend_registered_at", table_name="backend_user_snapshots"
    )
    op.drop_index("ix_backend_user_snapshots_registered_at", table_name="backend_user_snapshots")
    op.drop_table("backend_user_snapshots")
    op.drop_table("backend_user_snapshot_sources")
