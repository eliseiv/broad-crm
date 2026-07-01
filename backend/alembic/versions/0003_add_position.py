"""add position column to servers and ai_keys

Revision ID: 0003_add_position
Revises: 0002_create_ai_keys
Create Date: 2026-07-01

Добавляет колонку `position` (порядок карточек drag-and-drop, ADR-011) в обе
таблицы, делает backfill существующих строк по текущему визуальному порядку
(новые сверху → меньший `position`) и заменяет старый индекс сортировки
`created_at` новым индексом по `position` (03-data-model.md#миграция-0003_add_position).
`downgrade()` симметрично откатывает изменения (07-deployment.md#откат-миграций-бд).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_add_position"
down_revision: str | None = "0002_create_ai_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- servers: единый список, backfill по created_at DESC ---
    op.add_column(
        "servers",
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id, row_number() OVER (ORDER BY created_at DESC, id) - 1 AS pos
            FROM servers
        )
        UPDATE servers s SET position = ordered.pos FROM ordered WHERE s.id = ordered.id
        """
    )
    op.drop_index("ix_servers_created_at", table_name="servers")
    op.create_index("ix_servers_position", "servers", ["position"], unique=False)

    # --- ai_keys: backfill порядка ВНУТРИ провайдер-группы (PARTITION BY provider) ---
    op.add_column(
        "ai_keys",
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.execute(
        """
        WITH ordered AS (
            SELECT id,
                   row_number() OVER (PARTITION BY provider ORDER BY created_at DESC, id) - 1 AS pos
            FROM ai_keys
        )
        UPDATE ai_keys k SET position = ordered.pos FROM ordered WHERE k.id = ordered.id
        """
    )
    op.drop_index("ix_ai_keys_created_at", table_name="ai_keys")
    op.create_index(
        "ix_ai_keys_provider_position", "ai_keys", ["provider", "position"], unique=False
    )


def downgrade() -> None:
    # --- ai_keys: вернуть прежний индекс сортировки, снять колонку ---
    op.drop_index("ix_ai_keys_provider_position", table_name="ai_keys")
    op.create_index("ix_ai_keys_created_at", "ai_keys", [sa.text("created_at DESC")], unique=False)
    op.drop_column("ai_keys", "position")

    # --- servers: вернуть прежний индекс сортировки, снять колонку ---
    op.drop_index("ix_servers_position", table_name="servers")
    op.create_index("ix_servers_created_at", "servers", [sa.text("created_at DESC")], unique=False)
    op.drop_column("servers", "position")
