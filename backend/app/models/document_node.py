"""Модель таблицы `document_nodes` (03-data-model.md, modules/documents, ADR-059).

Единое self-referencing дерево папок и документов. **Первый soft-delete в проекте**
(`deleted_at`): удаление логическое — RAG обязан узнавать об удалениях. Все внутренние
выборки обязаны нести `WHERE deleted_at IS NULL`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DocumentNode(Base):
    """Узел дерева документов (папка или документ, 03-data-model.md#таблица-document_nodes).

    `content_version` инкрементируется ТОЛЬКО при изменении `content_md`/`name`; смена
    видимости/перемещение/soft-delete его НЕ меняют. `updated_at` обновляется при ЛЮБОЙ
    мутации (водяной знак внешнего sync). Enforcement — permission-based (`owner_id` —
    только автор для отображения, НЕ гейт).
    """

    __tablename__ = "document_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type IN ('folder', 'document')",
            name="ck_document_nodes_node_type",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255",
            name="ck_document_nodes_name_len",
        ),
        CheckConstraint(
            "node_type = 'document' OR content_md IS NULL",
            name="ck_document_nodes_folder_no_content",
        ),
        CheckConstraint(
            "visibility_mode IN ('inherit', 'restricted')",
            name="ck_document_nodes_visibility_mode",
        ),
        Index("ix_document_nodes_parent_id", "parent_id"),
        Index("ix_document_nodes_owner_id", "owner_id"),
        Index("ix_document_nodes_updated_at_id", "updated_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    node_type: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_nodes.id", ondelete="CASCADE", name="fk_document_nodes_parent_id"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_document_nodes_owner_id"),
        nullable=False,
    )
    visibility_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'inherit'")
    )
    # «Не включать в RAG»: наследуется вниз по дереву (эффективное исключение = флаг на
    # самом узле или любом предке). Смена флага — мутация (updated_at бампается,
    # content_version — нет), правится из модалки видимости (documents:share).
    rag_exclude: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    content_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
