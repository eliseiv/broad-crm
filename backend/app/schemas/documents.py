"""Схемы модуля «Документы» (04-api.md#documents / #external-documents, ADR-059/060).

Внутренний контур (JWT) — форма узла `DocumentNodeResponse` (без набора ролей в теле).
Read-сторона модалки видимости — `DocumentVisibilityResponse` (СОБСТВЕННЫЕ роли узла).
Внешний read-only контур (`X-API-Key`, RAG, ADR-060) — `ExternalDocumentNode` и производные
(поле `visibility_role_ids` несёт ЭФФЕКТИВНЫЙ набор ролей узла; иная семантика, чем
`role_ids` внутреннего контура — см. 04-api.md#external-documents).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FolderCreateRequest(BaseModel):
    """Тело `POST /api/documents/folders`."""

    parent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)


class DocumentCreateRequest(BaseModel):
    """Тело `POST /api/documents/documents`. `content_md` опц. (default `""`)."""

    parent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    content_md: str = ""


class DocumentPatchRequest(BaseModel):
    """Тело `PATCH /api/documents/nodes/{id}` — любое подмножество полей.

    «Переданное поле» определяется по `model_fields_set` (Pydantic v2): передача `name`/
    `content_md` инкрементирует `content_version`; `expected_version` — опциональный
    optimistic-lock (TD-064): при передаче и ≠ текущему → 409 document_node_conflict.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    content_md: str | None = None
    expected_version: int | None = None


class DocumentCopyRequest(BaseModel):
    """Тело `POST /api/documents/nodes/{id}/copy`.

    `target_parent_id` — куда положить копию (`null` = корень). НЕ передан
    (`model_fields_set`) → тот же `parent_id`, что у исходного узла.
    """

    target_parent_id: uuid.UUID | None = None


class DocumentVisibilityRequest(BaseModel):
    """Тело `PATCH /api/documents/nodes/{id}/visibility`.

    `restricted` → `role_ids` задаёт эффективный набор ролей (перезапись строк). `inherit`
    → `role_ids` игнорируется, строки узла удаляются (наследование/публичность).
    """

    visibility_mode: Literal["inherit", "restricted"]
    role_ids: list[uuid.UUID] = Field(default_factory=list)


class DocumentOrderRequest(BaseModel):
    """Тело `PATCH /api/documents/order` — полная перестановка уровня одного `parent_id`."""

    parent_id: uuid.UUID | None = None
    ids: list[uuid.UUID]


class DocumentNodeResponse(BaseModel):
    """Узел в ответах внутреннего API (04-api.md#documents «Форма узла в ответах»).

    `content_md` — только у документа и только в `GET /nodes/{id}` (в списках/дереве
    не отдаётся → `None`).
    """

    id: uuid.UUID
    node_type: str
    parent_id: uuid.UUID | None
    name: str
    content_md: str | None
    owner_id: uuid.UUID
    visibility_mode: str
    content_version: int
    position: int
    created_at: datetime
    updated_at: datetime


class DocumentVisibilityResponse(BaseModel):
    """Ответ `GET /api/documents/nodes/{id}/visibility` — предзаполнение модалки видимости.

    `role_ids` — **СОБСТВЕННЫЕ** роли узла (строки `document_node_roles` данного узла;
    `inherit` → `[]`), а НЕ эффективные/унаследованные. Симметрично телу write-контракта
    `PATCH /nodes/{id}/visibility` (04-api.md#documents) ⇒ форму можно отправить обратно
    без преобразования.
    """

    visibility_mode: Literal["inherit", "restricted"]
    role_ids: list[uuid.UUID]


class RoleRef(BaseModel):
    """Элемент `GET /api/documents/role-refs` — роль для модалки видимости (`{id, name}`)."""

    id: uuid.UUID
    name: str


# --- Внешний read-only контур (RAG, X-API-Key, ADR-060) ----------------------


class ExternalDocumentNode(BaseModel):
    """Элемент внешних списков/дельты (04-api.md#external-documents «Форма элемента»).

    `visibility_role_ids` — **ЭФФЕКТИВНЫЙ** набор ролей узла (резолюция наследования;
    публичный → `[]`) для фильтрации на стороне RAG; иная семантика, чем внутренний
    `role_ids`. `deleted_at` непуст у tombstone. `content_md` в списках НЕ отдаётся (только
    в `GET /{id}` — `ExternalDocumentDetail`).
    """

    id: uuid.UUID
    node_type: str
    parent_id: uuid.UUID | None
    name: str
    visibility_role_ids: list[uuid.UUID]
    content_version: int
    updated_at: datetime
    deleted_at: datetime | None


class ExternalDocumentDetail(ExternalDocumentNode):
    """Ответ `GET /api/external/documents/{id}` — узел с контентом (для документа)."""

    content_md: str | None


class ExternalDocumentListResponse(BaseModel):
    """Ответ `GET /api/external/documents` и `/changes` — страница синка + keyset-курсор."""

    items: list[ExternalDocumentNode]
    next_cursor: str | None


class ExternalDocumentAccessResponse(BaseModel):
    """Ответ `GET /api/external/documents/{id}/access` — эффективный уровень доступа узла."""

    id: uuid.UUID
    is_public: bool
    visibility_role_ids: list[uuid.UUID]
    content_version: int
