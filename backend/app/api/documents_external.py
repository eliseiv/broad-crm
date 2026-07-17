"""Внешний read-only контур модуля «Документы» для RAG-базы ИИ (ADR-060, 04-api.md#external).

Машинные эндпоинты `/api/external/documents/*`: аутентификация — статический `X-API-Key`
(env `DOCUMENTS_API_KEY`), **без JWT, CSRF-exempt**. Порядок проверок (dependency
`require_documents_api_key`): пустой ключ → 503 documents_external_not_configured →
неверный/отсутствующий заголовок → 401 not_authenticated. Регистрирует **только GET**
(read-only гарантия ADR-060 §3 — инвариант ревью). Каждый ответ несёт `Cache-Control:
no-store`. Машина видит ВСЕ узлы (обходит per-role фильтр); каждый элемент несёт
ЭФФЕКТИВНЫЙ `visibility_role_ids` — RAG фильтрует по роли на своей стороне.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.deps import DocumentServiceDep
from app.infra.documents_api_key import DocumentsApiKeyDep
from app.schemas.documents import (
    ExternalDocumentAccessResponse,
    ExternalDocumentDetail,
    ExternalDocumentListResponse,
)

router = APIRouter(prefix="/external/documents", tags=["documents-external"])

_NO_STORE = "no-store"

LimitQuery = Annotated[int, Query(ge=1, le=500)]
UpdatedAfterQuery = Annotated[datetime | None, Query()]
IncludeDeletedQuery = Annotated[bool, Query()]
CursorQuery = Annotated[str | None, Query()]
SinceQuery = Annotated[datetime, Query()]


@router.get("", response_model=ExternalDocumentListResponse)
async def list_documents(
    service: DocumentServiceDep,
    _key: DocumentsApiKeyDep,
    response: Response,
    updated_after: UpdatedAfterQuery = None,
    include_deleted: IncludeDeletedQuery = False,
    cursor: CursorQuery = None,
    limit: LimitQuery = 100,
) -> ExternalDocumentListResponse:
    """Список для синхронизации (keyset `(updated_at,id)` ASC). Битый cursor/limit → 400."""
    response.headers["Cache-Control"] = _NO_STORE
    return await service.list_external(
        updated_after=updated_after,
        include_deleted=include_deleted,
        cursor_token=cursor,
        limit=limit,
    )


@router.get("/changes", response_model=ExternalDocumentListResponse)
async def list_changes(
    service: DocumentServiceDep,
    _key: DocumentsApiKeyDep,
    response: Response,
    since: SinceQuery,
    cursor: CursorQuery = None,
    limit: LimitQuery = 100,
) -> ExternalDocumentListResponse:
    """Дельта с водяного знака `since` (изменённые + tombstones). Нет/битый `since` → 400."""
    response.headers["Cache-Control"] = _NO_STORE
    return await service.changes_external(since=since, cursor_token=cursor, limit=limit)


@router.get("/{node_id}/access", response_model=ExternalDocumentAccessResponse)
async def get_access(
    node_id: uuid.UUID,
    service: DocumentServiceDep,
    _key: DocumentsApiKeyDep,
    response: Response,
) -> ExternalDocumentAccessResponse:
    """Эффективный уровень доступа узла. Не существует → 404 document_node_not_found."""
    response.headers["Cache-Control"] = _NO_STORE
    return await service.get_external_access(node_id)


@router.get("/{node_id}", response_model=ExternalDocumentDetail)
async def get_document(
    node_id: uuid.UUID,
    service: DocumentServiceDep,
    _key: DocumentsApiKeyDep,
    response: Response,
) -> ExternalDocumentDetail:
    """Полный узел с контентом. Удалён → 410 document_node_gone; не существовал → 404."""
    response.headers["Cache-Control"] = _NO_STORE
    return await service.get_external(node_id)
