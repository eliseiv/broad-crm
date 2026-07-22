"""Модель таблицы `document_attachments` (03-data-model.md, modules/documents, ADR-068).

**Разделение носителей (нормативно):** строка — источник истины о существовании и свойствах
вложения; файл на volume — только байты. Строки без файла быть не может (порядок записи —
ADR-068 §2); файл без строки возможен как аномалия, никем не адресуем (GC — TD-076).

`updated_at` нет НАМЕРЕННО: вложение иммутабельно — перезаписи файла не существует,
«замена картинки» = новая загрузка (новый `id`) + правка `content_md` (`content_version += 1`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
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

# Whitelist типов — ровно четыре растровых формата (ADR-068 §2.3). SVG исключён нормативно:
# активный документ (скрипты, `<foreignObject>`), отдаваемый с нашего origin ⇒ XSS-вектор,
# который не закрывают ни `nosniff`, ни CSP страницы.
ALLOWED_IMAGE_MIME: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

_MIME_CHECK = "mime IN ('image/png','image/jpeg','image/webp','image/gif')"


class DocumentAttachment(Base):
    """Метаданные изображения, вставленного в документ (ADR-068 §1)."""

    __tablename__ = "document_attachments"
    __table_args__ = (
        CheckConstraint(
            "char_length(filename) BETWEEN 1 AND 255",
            name="ck_document_attachments_filename_len",
        ),
        CheckConstraint(_MIME_CHECK, name="ck_document_attachments_mime"),
        CheckConstraint("size_bytes > 0", name="ck_document_attachments_size"),
        CheckConstraint("char_length(checksum) = 64", name="ck_document_attachments_checksum"),
        Index("ix_document_attachments_node_id", "document_node_id"),
        Index("ix_document_attachments_created_by", "created_by"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "document_nodes.id",
            ondelete="CASCADE",
            name="fk_document_attachments_node_id",
        ),
        nullable=False,
    )
    # Исходное имя — ТОЛЬКО для `Content-Disposition`/alt; в пути на диске НЕ участвует.
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    # Тип, определённый ПО СОДЕРЖИМОМУ (magic bytes) при загрузке; управляет `Content-Type`
    # отдачи и расширением файла.
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # sha256 содержимого (hex lowercase) — он же `ETag` отдачи. UNIQUE не вводится
    # (дедупликации нет: одинаковый файл в двух узлах хранится дважды).
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_document_attachments_created_by"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
