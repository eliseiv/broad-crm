"""Репозиторий вложений документов (SQLAlchemy 2.0 async, ADR-068).

Доступ к `document_attachments`: точечное чтение, вставка строки метаданных, выборка
вложений набора узлов (копирование поддерева), удаление строки. Только `flush` — `commit`
выполняет сервис (порядок «строка → файл → commit» нормативен, ADR-068 §2).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_attachment import DocumentAttachment


class DocumentAttachmentRepository:
    """CRUD метаданных вложений документов."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (транзакцией управляет сервис)."""
        return self._session

    async def get_by_id(self, attachment_id: uuid.UUID) -> DocumentAttachment | None:
        """Вложение по id или None."""
        return await self._session.get(DocumentAttachment, attachment_id)

    async def list_for_nodes(self, node_ids: list[uuid.UUID]) -> list[DocumentAttachment]:
        """Вложения набора узлов (копирование поддерева), детерминированный порядок."""
        if not node_ids:
            return []
        stmt = (
            select(DocumentAttachment)
            .where(DocumentAttachment.document_node_id.in_(node_ids))
            .order_by(DocumentAttachment.created_at.asc(), DocumentAttachment.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self,
        *,
        document_node_id: uuid.UUID,
        filename: str,
        mime: str,
        size_bytes: int,
        checksum: str,
        created_by: uuid.UUID,
    ) -> DocumentAttachment:
        """Вставляет строку и делает `flush` ⇒ становится известен `id` (и шард-путь)."""
        attachment = DocumentAttachment(
            document_node_id=document_node_id,
            filename=filename,
            mime=mime,
            size_bytes=size_bytes,
            checksum=checksum,
            created_by=created_by,
        )
        self._session.add(attachment)
        await self._session.flush()
        await self._session.refresh(attachment)
        return attachment

    async def create_many(self, rows: list[dict[str, Any]]) -> list[DocumentAttachment]:
        """Bulk-вставка строк (копирование поддерева): один `add_all` + ОДИН `flush`.

        Порядок результата соответствует порядку `rows`. `refresh` не делается намеренно:
        `id` заполняется на `flush` (PK возвращается `RETURNING`), а `created_at` при
        копировании не сериализуется — лишний round-trip на каждую строку не нужен.
        """
        if not rows:
            return []
        attachments = [DocumentAttachment(**row) for row in rows]
        self._session.add_all(attachments)
        await self._session.flush()
        return attachments

    async def delete_by_id(self, attachment_id: uuid.UUID) -> None:
        """Удаляет строку (файл снимает сервис ПОСЛЕ успешного `commit`)."""
        await self._session.execute(
            delete(DocumentAttachment).where(DocumentAttachment.id == attachment_id)
        )
