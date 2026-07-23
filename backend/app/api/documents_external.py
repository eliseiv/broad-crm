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

from app.api.deps import DbSession, DocumentServiceDep
from app.domain.permissions import CATALOG
from app.errors import AppError
from app.infra.documents_api_key import DocumentsApiKeyDep
from app.repositories.mail_telegram_link_repository import MailTelegramLinkRepository
from app.repositories.sms_telegram_link_repository import SmsTelegramLinkRepository
from app.repositories.user_repository import UserRepository
from app.schemas.documents import (
    ExternalDocumentAccessResponse,
    ExternalDocumentDetail,
    ExternalDocumentListResponse,
    ExternalUserAccessResponse,
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


@router.get("/user-access/{telegram_user_id}", response_model=ExternalUserAccessResponse)
async def get_user_access(
    telegram_user_id: int,
    session: DbSession,
    _key: DocumentsApiKeyDep,
    response: Response,
) -> ExternalUserAccessResponse:
    """Резолв пользователя CRM по telegram id (этап 2 RAG-бота).

    Порядок: активный sms-линк → активный mail-линк (с user_id) → пользователь активен и не
    системный. Не найден/неактивен → 404 user_not_linked (боту это «доступа нет»).
    `sees_all_documents` — роль покрывает полный каталог прав (admin-уровень CRM).
    """
    response.headers["Cache-Control"] = _NO_STORE

    user_id = None
    sms_link = await SmsTelegramLinkRepository(session).get_active_by_telegram_user_id(
        telegram_user_id
    )
    if sms_link is not None:
        user_id = sms_link.user_id
    else:
        mail_link = await MailTelegramLinkRepository(session).get_by_telegram_user_id(
            telegram_user_id
        )
        if mail_link is not None and mail_link.dead_at is None and mail_link.user_id is not None:
            user_id = mail_link.user_id

    user = await UserRepository(session).get_by_id(user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise AppError(
            status_code=404,
            code="user_not_linked",
            message="Пользователь Telegram не привязан к активному пользователю CRM",
        )

    permissions = user.role.permissions or {}
    sees_all = all(
        set(actions) <= set(permissions.get(page, [])) for page, actions in CATALOG.items()
    )
    return ExternalUserAccessResponse(
        user_id=user.id,
        role_id=user.role_id,
        role_name=user.role.name,
        sees_all_documents=sees_all,
    )


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
