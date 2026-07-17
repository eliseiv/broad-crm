r"""document_nodes — единое дерево папок/документов модуля «Документы» (ADR-059)

Revision ID: 0029_document_nodes
Revises: 0028_mail_dedup_orphan_cleanup
Create Date: 2026-07-17

Создаёт таблицу `document_nodes` (03-data-model.md#таблица-document_nodes):

- **4 CHECK** — `node_type ∈ {folder, document}`; `name` 1..255; папка не хранит контент
  (`node_type = 'document' OR content_md IS NULL`); `visibility_mode ∈ {inherit, restricted}`.
- **`parent_id` FK на себя `ON DELETE CASCADE`** — self-referencing дерево (`NULL` = корень);
  каскад страхует целостность (в норме удаление логическое — `deleted_at`).
- **`owner_id` FK → users `ON DELETE RESTRICT`** — автор для отображения (НЕ гейт);
  удаление пользователя-автора заблокировано, пока есть его узлы.
- **Первый soft-delete в проекте** (`deleted_at`): удаление логическое (tombstone для RAG).
- **3 индекса** — `parent_id` (обход дерева + каскад), `owner_id` (reverse-lookup +
  RESTRICT), `(updated_at, id)` (компаундный keyset внешнего RAG-sync; НЕ частичный —
  пагинация обязана включать tombstones).

`revision = "0029_document_nodes"` (19 символов ≤ 32, 03-data-model.md#1-revision-id).
Backfill НЕ нужен (таблица стартует пустой). `downgrade()` — рабочий (DROP TABLE).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_document_nodes"
down_revision: str | None = "0028_mail_dedup_orphan_cleanup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_nodes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("node_type", sa.Text(), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "visibility_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'inherit'"),
        ),
        sa.Column(
            "content_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_document_nodes"),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["document_nodes.id"],
            name="fk_document_nodes_parent_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_document_nodes_owner_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "node_type IN ('folder', 'document')",
            name="ck_document_nodes_node_type",
        ),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255",
            name="ck_document_nodes_name_len",
        ),
        sa.CheckConstraint(
            "node_type = 'document' OR content_md IS NULL",
            name="ck_document_nodes_folder_no_content",
        ),
        sa.CheckConstraint(
            "visibility_mode IN ('inherit', 'restricted')",
            name="ck_document_nodes_visibility_mode",
        ),
    )
    op.create_index("ix_document_nodes_parent_id", "document_nodes", ["parent_id"])
    op.create_index("ix_document_nodes_owner_id", "document_nodes", ["owner_id"])
    op.create_index(
        "ix_document_nodes_updated_at_id",
        "document_nodes",
        ["updated_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_nodes_updated_at_id", table_name="document_nodes")
    op.drop_index("ix_document_nodes_owner_id", table_name="document_nodes")
    op.drop_index("ix_document_nodes_parent_id", table_name="document_nodes")
    op.drop_table("document_nodes")
