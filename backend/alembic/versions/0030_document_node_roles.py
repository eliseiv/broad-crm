r"""document_node_roles — видимость узел ↔ роль модуля «Документы» (ADR-059)

Revision ID: 0030_document_node_roles
Revises: 0029_document_nodes
Create Date: 2026-07-17

Создаёт таблицу `document_node_roles` (03-data-model.md#таблица-document_node_roles) —
набор ролей, которым виден `restricted`-узел (образец `user_channel_teams`):

- **composite `PRIMARY KEY (node_id, role_id)`** — пара не дублируется; префикс `(node_id)`
  покрывает сборку эффективного набора ролей узла при резолве видимости.
- **обе FK `ON DELETE CASCADE`** — `node_id → document_nodes` (при физическом GC узла),
  `role_id → roles` (hard-delete роли снимает её из эффективных наборов автоматически).
- **`ix_document_node_roles_role_id` обязателен** — под каскад при удалении роли (иначе
  seq-scan) и под обратную выборку «какие узлы видит роль».

`revision = "0030_document_node_roles"` (24 символа ≤ 32, 03-data-model.md#1-revision-id).
Backfill НЕ нужен (таблица стартует пустой). `downgrade()` — рабочий (DROP TABLE).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_document_node_roles"
down_revision: str | None = "0029_document_nodes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_node_roles",
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("node_id", "role_id", name="pk_document_node_roles"),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["document_nodes.id"],
            name="fk_document_node_roles_node_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_document_node_roles_role_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_document_node_roles_role_id",
        "document_node_roles",
        ["role_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_node_roles_role_id", table_name="document_node_roles")
    op.drop_table("document_node_roles")
