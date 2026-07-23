"""«Не включать в RAG»: document_nodes.rag_exclude (наследуется вниз по дереву).

Чистый expand: NOT NULL c server_default false на существующей таблице. Код N-1 колонку
не знает и не пишет — дефолт сохраняет прежнее поведение (всё индексируется).

Revision ID: 0033_document_nodes_rag_excl
Revises: 0032_document_attachments
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033_document_nodes_rag_excl"
down_revision: str | None = "0032_document_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_nodes",
        sa.Column("rag_exclude", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("document_nodes", "rag_exclude")
