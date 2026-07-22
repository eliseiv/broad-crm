"""Роутер модуля «Документы» (04-api.md#documents, ADR-059).

Тонкий HTTP-слой: RBAC-гейт `require("documents", <action>)` + per-node фильтр видимости
(через `DocumentScopeDep`) + вызов сервиса. Маппинг метод→действие — 04-api.md#documents.
Внешний read-only контур (`X-API-Key`, RAG) — спринт 2, здесь НЕ регистрируется.
"""

from __future__ import annotations

import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import (
    DocumentAttachmentServiceDep,
    DocumentScopeDep,
    DocumentServiceDep,
    Principal,
    require,
)
from app.errors import validation_error
from app.schemas.documents import (
    DocumentAttachmentResponse,
    DocumentCopyRequest,
    DocumentCreateRequest,
    DocumentNodeResponse,
    DocumentOrderRequest,
    DocumentPatchRequest,
    DocumentVisibilityRequest,
    DocumentVisibilityResponse,
    FolderCreateRequest,
    RoleRef,
)
from app.services.document_attachment_service import AttachmentFile

router = APIRouter(prefix="/documents", tags=["documents"])

ViewDep = Annotated[Principal, Depends(require("documents", "view"))]
CreateDep = Annotated[Principal, Depends(require("documents", "create"))]
EditDep = Annotated[Principal, Depends(require("documents", "edit"))]
DeleteDep = Annotated[Principal, Depends(require("documents", "delete"))]
ShareDep = Annotated[Principal, Depends(require("documents", "share"))]

ParentIdQuery = Annotated[uuid.UUID | None, Query()]

# Кэш отдачи вложения (ADR-068 §7). `public` ЗАПРЕЩЁН: ответ зависит от прав
# запрашивающего (per-node видимость) ⇒ shared-кэш прокси отдал бы картинку постороннему.
_ATTACHMENT_CACHE_CONTROL = "private, max-age=300, must-revalidate"


def _attachment_headers(attachment: AttachmentFile) -> dict[str, str]:
    """Заголовки отдачи: `ETag` = checksum, `Cache-Control: private…`, inline-disposition.

    `Content-Type` берётся ИЗ БД (`mime`, проверенный по содержимому при загрузке), а не из
    имени файла; `X-Content-Type-Options: nosniff` уже ставит middleware на весь `/api`.
    """
    encoded = quote(attachment.filename, safe="")
    return {
        "ETag": f'"{attachment.checksum}"',
        "Cache-Control": _ATTACHMENT_CACHE_CONTROL,
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded}",
    }


def _etag_matches(if_none_match: str | None, checksum: str) -> bool:
    """Сверка `If-None-Match` с `"<checksum>"` (учитывает `*`, список и слабый префикс `W/`)."""
    if not if_none_match:
        return False
    for raw in if_none_match.split(","):
        candidate = raw.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate.strip('"') == checksum:
            return True
    return False


def _parse_optional_uuid(raw: str | None, field: str) -> uuid.UUID | None:
    """Парсит опциональный uuid из multipart-формы; пусто/пробелы → None; мусор → 400."""
    if raw is None or not raw.strip():
        return None
    try:
        return uuid.UUID(raw.strip())
    except ValueError as exc:
        raise validation_error(
            "Некорректный идентификатор",
            details=[{"field": field, "message": "Ожидается UUID"}],
        ) from exc


@router.get("/tree", response_model=list[DocumentNodeResponse])
async def get_tree(
    service: DocumentServiceDep, scope: DocumentScopeDep, _p: ViewDep
) -> list[DocumentNodeResponse]:
    """Всё видимое дерево (папки+документы, без `content_md`)."""
    return await service.get_tree(scope)


@router.get("/nodes", response_model=list[DocumentNodeResponse])
async def list_nodes(
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    _p: ViewDep,
    parent_id: ParentIdQuery = None,
) -> list[DocumentNodeResponse]:
    """Видимые дети уровня (`parent_id` пуст → корень), без `content_md`."""
    return await service.get_children(scope, parent_id)


@router.get("/role-refs", response_model=list[RoleRef])
async def list_role_refs(service: DocumentServiceDep, _p: ShareDep) -> list[RoleRef]:
    """Роли для модалки видимости (`{id, name}`), гейт `documents:share`."""
    return await service.list_role_refs()


@router.get("/nodes/{node_id}/visibility", response_model=DocumentVisibilityResponse)
async def get_visibility(
    node_id: uuid.UUID, service: DocumentServiceDep, scope: DocumentScopeDep, _p: ShareDep
) -> DocumentVisibilityResponse:
    """Собственные настройки видимости узла для модалки (гейт `documents:share`). Невидим → 404."""
    return await service.get_visibility(node_id, scope=scope)


@router.get("/nodes/{node_id}", response_model=DocumentNodeResponse)
async def get_node(
    node_id: uuid.UUID, service: DocumentServiceDep, scope: DocumentScopeDep, _p: ViewDep
) -> DocumentNodeResponse:
    """Один узел (+`content_md` для документа). Невидим → 404."""
    return await service.get_node(scope, node_id)


@router.post("/folders", response_model=DocumentNodeResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreateRequest,
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    principal: CreateDep,
) -> DocumentNodeResponse:
    """Создать папку (201)."""
    return await service.create_folder(payload, scope=scope, owner_id=principal.user_id)


@router.post("/documents", response_model=DocumentNodeResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    payload: DocumentCreateRequest,
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    principal: CreateDep,
) -> DocumentNodeResponse:
    """Создать документ (201)."""
    return await service.create_document(payload, scope=scope, owner_id=principal.user_id)


@router.post("/upload", response_model=DocumentNodeResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    principal: CreateDep,
    file: Annotated[UploadFile, File()],
    parent_id: Annotated[str | None, Form()] = None,
    name: Annotated[str | None, Form()] = None,
) -> DocumentNodeResponse:
    """Загрузка `.md`-файла как документа (multipart). Не `.md`/размер/битый UTF-8 → 422."""
    parsed_parent = _parse_optional_uuid(parent_id, "parent_id")
    return await service.upload_document(
        file=file,
        parent_id=parsed_parent,
        name=name,
        scope=scope,
        owner_id=principal.user_id,
    )


@router.post(
    "/nodes/{node_id}/copy",
    response_model=DocumentNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def copy_node(
    node_id: uuid.UUID,
    payload: DocumentCopyRequest,
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    principal: CreateDep,
) -> DocumentNodeResponse:
    """Рекурсивная копия узла/поддерева (201). Цикл → 422."""
    return await service.copy_node(node_id, payload, scope=scope, owner_id=principal.user_id)


@router.post(
    "/nodes/{node_id}/attachments",
    response_model=DocumentAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    node_id: uuid.UUID,
    service: DocumentAttachmentServiceDep,
    scope: DocumentScopeDep,
    principal: EditDep,
    file: Annotated[UploadFile, File()],
) -> DocumentAttachmentResponse:
    """Загрузка изображения в документ (multipart), гейт `documents:edit` + видимость узла.

    Гейт `edit`, а не `create`: вложение — часть контента СУЩЕСТВУЮЩЕГО узла, тогда как
    `create` в этом модуле означает «создать узел». Папка → 422; тип/размер → 422
    `document_attachment_invalid`.
    """
    return await service.upload(node_id, file=file, scope=scope, created_by=principal.user_id)


@router.get("/attachments/{attachment_id}")
async def get_attachment(
    attachment_id: uuid.UUID,
    request: Request,
    service: DocumentAttachmentServiceDep,
    scope: DocumentScopeDep,
    _p: ViewDep,
) -> Response:
    """Байты изображения; гейт `documents:view` + тот же фильтр видимости узла-владельца.

    Нет вложения / узел невидим по роли / узел soft-deleted → единый
    `404 document_attachment_not_found`. `If-None-Match` совпал → `304`.
    """
    attachment = await service.get_file(attachment_id, scope=scope)
    headers = _attachment_headers(attachment)
    if _etag_matches(request.headers.get("if-none-match"), attachment.checksum):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    # `stat_result` снят сервисом под гейтами и передаётся явно: иначе `FileResponse`
    # сделал бы собственный `stat` уже после проверок и на гонке с `DELETE` отдал бы
    # `RuntimeError` (500) вместо контрактного 404.
    return FileResponse(
        path=attachment.path,
        media_type=attachment.mime,
        headers=headers,
        stat_result=attachment.stat_result,
    )


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID,
    service: DocumentAttachmentServiceDep,
    scope: DocumentScopeDep,
    _p: EditDep,
) -> Response:
    """Удаление вложения: строка в транзакции, файл — после `commit` (гейт `documents:edit`)."""
    await service.delete(attachment_id, scope=scope)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_nodes(
    payload: DocumentOrderRequest,
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    _p: EditDep,
) -> Response:
    """Полная перестановка уровня (`position = 0..N-1`)."""
    await service.reorder(payload.parent_id, payload.ids, scope=scope)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/nodes/{node_id}/visibility", response_model=DocumentNodeResponse)
async def set_visibility(
    node_id: uuid.UUID,
    payload: DocumentVisibilityRequest,
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    _p: ShareDep,
) -> DocumentNodeResponse:
    """Смена видимости узла (гейт `documents:share`)."""
    return await service.set_visibility(node_id, payload, scope=scope)


@router.patch("/nodes/{node_id}", response_model=DocumentNodeResponse)
async def patch_node(
    node_id: uuid.UUID,
    payload: DocumentPatchRequest,
    service: DocumentServiceDep,
    scope: DocumentScopeDep,
    _p: EditDep,
) -> DocumentNodeResponse:
    """Rename и/или правка контента. `content_version += 1` при правке."""
    return await service.patch_node(node_id, payload, scope=scope)


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: uuid.UUID, service: DocumentServiceDep, scope: DocumentScopeDep, _p: DeleteDep
) -> Response:
    """Soft-delete узла; папка — каскад поддерева."""
    await service.delete_node(node_id, scope=scope)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
