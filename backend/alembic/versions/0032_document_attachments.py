r"""document_attachments — метаданные изображений документов (ADR-068)

Revision ID: 0032_document_attachments
Revises: 0031_servers_ssh_key_auth
Create Date: 2026-07-22

Создаёт таблицу `document_attachments` (03-data-model.md#таблица-document_attachments-adr-068):

- **4 CHECK** — `filename` 1..255; `mime` ∈ whitelist `png/jpeg/webp/gif` (**SVG исключён
  нормативно** — активный документ = XSS-вектор с нашего origin); `size_bytes > 0`;
  `checksum` ровно 64 символа (sha256 hex). Верхняя граница размера — `DOCUMENTS_MAX_IMAGE_BYTES`
  (env) и проверяется в сервисе: в CHECK её вшивать нельзя, она менялась бы миграцией.
- **`document_node_id` FK → document_nodes `ON DELETE CASCADE`** — вложение живёт ровно
  в одном узле; soft-delete узла строки НЕ трогает (каскад работает при физическом GC).
- **`created_by` FK → users `ON DELETE RESTRICT`** — симметрично `document_nodes.owner_id`.
- **2 индекса** — `document_node_id` (вложения узла: копирование поддерева, GC, каскад),
  `created_by` (обслуживание RESTRICT при удалении учётки, иначе seq-scan). Индекс по
  `checksum` не заводится: дедупликации нет, поиск по контрольной сумме — не сценарий.

`revision = "0032_document_attachments"` (25 символов ≤ 32, 03-data-model.md#1-revision-id).
Backfill НЕ нужен (таблица стартует пустой). `downgrade()` — рабочий (DROP TABLE).

⚠️ **Файлы на volume миграция НЕ трогает** (Alembic диском не управляет): после отката
байты изображений остаются осиротевшими под `DOCUMENTS_ATTACHMENTS_DIR` и убираются
вручную/GC (TD-076). Это зафиксировано осознанно, а не недосмотр.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_document_attachments"
down_revision: str | None = "0031_servers_ssh_key_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_attachments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_attachments"),
        sa.ForeignKeyConstraint(
            ["document_node_id"],
            ["document_nodes.id"],
            name="fk_document_attachments_node_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_document_attachments_created_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "char_length(filename) BETWEEN 1 AND 255",
            name="ck_document_attachments_filename_len",
        ),
        sa.CheckConstraint(
            "mime IN ('image/png','image/jpeg','image/webp','image/gif')",
            name="ck_document_attachments_mime",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_document_attachments_size"),
        sa.CheckConstraint(
            "char_length(checksum) = 64",
            name="ck_document_attachments_checksum",
        ),
    )
    op.create_index(
        "ix_document_attachments_node_id",
        "document_attachments",
        ["document_node_id"],
    )
    op.create_index(
        "ix_document_attachments_created_by",
        "document_attachments",
        ["created_by"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_attachments_created_by", table_name="document_attachments")
    op.drop_index("ix_document_attachments_node_id", table_name="document_attachments")
    op.drop_table("document_attachments")
