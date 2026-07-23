"""Бизнес-логика модуля «Документы» (modules/documents, 04-api.md#documents, ADR-059).

Управляет транзакцией/commit, резолюцией видимости по роли (анти-энумерация → 404),
рекурсивной копией поддерева, каскадным soft-delete, инкрементом `content_version`.
Маппит модель → схему. `owner_id` — только автор для отображения (enforcement
permission-based: гейт `documents:<action>` + видимость, а НЕ владение).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import UploadFile, status

from app.config import Settings
from app.domain.documents import (
    DocumentCursorError,
    DocumentScope,
    decode_document_cursor,
    encode_document_cursor,
)
from app.errors import (
    AppError,
    document_copy_cycle,
    document_node_conflict,
    document_node_gone,
    document_node_not_found,
    document_upload_invalid,
    unprocessable,
    validation_error,
)
from app.logging import get_logger
from app.models.document_node import DocumentNode
from app.repositories.document_repository import DocumentRepository
from app.repositories.role_repository import RoleRepository
from app.schemas.documents import (
    DocumentCopyRequest,
    DocumentCreateRequest,
    DocumentNodeResponse,
    DocumentPatchRequest,
    DocumentVisibilityRequest,
    DocumentVisibilityResponse,
    ExternalDocumentAccessResponse,
    ExternalDocumentDetail,
    ExternalDocumentListResponse,
    ExternalDocumentNode,
    FolderCreateRequest,
    RoleRef,
)
from app.services.document_attachment_service import (
    ATTACHMENT_URL_PREFIX,
    DocumentAttachmentService,
)
from app.services.document_visibility import ensure_visible_node

logger = get_logger(__name__)

_MD_SUFFIX = ".md"


def _content_md_error(message: str) -> AppError:
    """422 с кодом `validation_error`, поле `content_md` (лимит размера / контент у папки).

    Код контракта — `validation_error` (НЕ `unprocessable`), статус 422 (04-api.md#documents,
    README §Лимит размера markdown). Отдельный от `errors.validation_error` (тот 400).
    """
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_error",
        message=message,
        details=[{"field": "content_md", "message": message}],
    )


class DocumentService:
    """CRUD дерева документов + резолюция видимости, copy, soft-delete."""

    def __init__(
        self,
        repository: DocumentRepository,
        roles: RoleRepository,
        settings: Settings,
        attachments: DocumentAttachmentService,
    ) -> None:
        self._repo = repository
        self._roles = roles
        self._settings = settings
        self._attachments = attachments

    # --- Чтение -----------------------------------------------------------

    async def get_tree(self, scope: DocumentScope) -> list[DocumentNodeResponse]:
        """Всё видимое дерево (без `content_md`), порядок уровня — position/created_at/id."""
        nodes = await self._repo.list_visible_tree(scope)
        return [self._serialize(n, include_content=False) for n in nodes]

    async def get_children(
        self, scope: DocumentScope, parent_id: uuid.UUID | None
    ) -> list[DocumentNodeResponse]:
        """Видимые дети уровня `parent_id` (None = корень), без `content_md`."""
        nodes = await self._repo.list_visible_children(scope, parent_id)
        return [self._serialize(n, include_content=False) for n in nodes]

    async def get_node(self, scope: DocumentScope, node_id: uuid.UUID) -> DocumentNodeResponse:
        """Один узел (+`content_md` для документа). Невидим → 404 (анти-энумерация)."""
        node = await self._ensure_visible(scope, node_id)
        return self._serialize(node, include_content=True)

    async def get_visibility(
        self, node_id: uuid.UUID, *, scope: DocumentScope
    ) -> DocumentVisibilityResponse:
        """Собственные настройки видимости узла для предзаполнения модалки (04-api.md#documents).

        `role_ids` — СОБСТВЕННЫЕ роли узла (`document_node_roles` данного узла), НЕ
        эффективные/унаследованные; `inherit` → `[]`. Невидим → 404 (анти-энумерация).
        """
        node = await self._ensure_visible(scope, node_id)
        mode: Literal["inherit", "restricted"] = (
            "restricted" if node.visibility_mode == "restricted" else "inherit"
        )
        role_ids: list[uuid.UUID] = []
        if mode == "restricted":
            role_ids = sorted(await self._repo.node_role_ids(node_id), key=str)
        return DocumentVisibilityResponse(
            visibility_mode=mode, role_ids=role_ids, rag_exclude=node.rag_exclude
        )

    # --- Создание ---------------------------------------------------------

    async def create_folder(
        self, payload: FolderCreateRequest, *, scope: DocumentScope, owner_id: uuid.UUID
    ) -> DocumentNodeResponse:
        """Создаёт папку. `parent_id` — видимый узел-папка или null."""
        await self._resolve_parent(scope, payload.parent_id)
        node = await self._repo.create(
            node_type="folder",
            parent_id=payload.parent_id,
            name=payload.name,
            content_md=None,
            owner_id=owner_id,
        )
        await self._repo.session.commit()
        logger.info("document_folder_created", node_id=str(node.id))
        return self._serialize(node, include_content=False)

    async def create_document(
        self, payload: DocumentCreateRequest, *, scope: DocumentScope, owner_id: uuid.UUID
    ) -> DocumentNodeResponse:
        """Создаёт документ (контент опц., default `""`; размер ≤ лимита)."""
        await self._resolve_parent(scope, payload.parent_id)
        self._ensure_md_size(payload.content_md)
        node = await self._repo.create(
            node_type="document",
            parent_id=payload.parent_id,
            name=payload.name,
            content_md=payload.content_md,
            owner_id=owner_id,
        )
        await self._repo.session.commit()
        logger.info("document_created", node_id=str(node.id))
        return self._serialize(node, include_content=False)

    async def upload_document(
        self,
        *,
        file: UploadFile,
        parent_id: uuid.UUID | None,
        name: str | None,
        scope: DocumentScope,
        owner_id: uuid.UUID,
    ) -> DocumentNodeResponse:
        """Загрузка `.md`-файла как документа. Не `.md`/размер/битый UTF-8 → 422."""
        filename = file.filename or ""
        if not filename.lower().endswith(_MD_SUFFIX):
            raise document_upload_invalid()
        raw = await file.read()
        if len(raw) > self._settings.documents_max_md_bytes:
            raise document_upload_invalid()
        try:
            content_md = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise document_upload_invalid() from exc

        doc_name = (name or "").strip() or filename[: -len(_MD_SUFFIX)]
        if not (1 <= len(doc_name) <= 255):
            raise document_upload_invalid()

        await self._resolve_parent(scope, parent_id)
        node = await self._repo.create(
            node_type="document",
            parent_id=parent_id,
            name=doc_name,
            content_md=content_md,
            owner_id=owner_id,
        )
        await self._repo.session.commit()
        logger.info("document_uploaded", node_id=str(node.id))
        return self._serialize(node, include_content=False)

    # --- Правка -----------------------------------------------------------

    async def patch_node(
        self, node_id: uuid.UUID, payload: DocumentPatchRequest, *, scope: DocumentScope
    ) -> DocumentNodeResponse:
        """Rename и/или правка контента. `content_version += 1` при передаче name/content."""
        node = await self._ensure_visible(scope, node_id)
        fields = payload.model_fields_set
        set_name = "name" in fields and payload.name is not None
        set_content = "content_md" in fields

        if set_content and node.node_type == "folder":
            raise _content_md_error("Папка не хранит контент")
        if (
            payload.expected_version is not None
            and payload.expected_version != node.content_version
        ):
            raise document_node_conflict()
        if set_content and payload.content_md is not None:
            self._ensure_md_size(payload.content_md)

        bump = set_name or set_content
        node = await self._repo.apply_patch(
            node,
            name=payload.name if set_name else None,
            content_md=payload.content_md,
            set_content=set_content,
            bump_version=bump,
        )
        await self._repo.session.commit()
        logger.info("document_patched", node_id=str(node.id))
        return self._serialize(node, include_content=False)

    async def set_visibility(
        self, node_id: uuid.UUID, payload: DocumentVisibilityRequest, *, scope: DocumentScope
    ) -> DocumentNodeResponse:
        """Смена видимости. `restricted` → набор ролей; `inherit` → строки удаляются."""
        node = await self._ensure_visible(scope, node_id)
        if payload.visibility_mode == "restricted":
            role_ids = list(dict.fromkeys(payload.role_ids))
            if role_ids:
                existing = await self._roles.existing_ids(set(role_ids))
                if set(role_ids) - existing:
                    # 422 validation_error (НЕ 400): семантически некорректное значение —
                    # ссылка на несуществующую роль (04-api.md#documents §PATCH visibility,
                    # строка 1573). По образцу `_content_md_error`, а не `errors.validation_error`
                    # (та отдаёт 400 для структурных/диапазонных ошибок формы).
                    raise AppError(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        code="validation_error",
                        message="Указана несуществующая роль",
                        details=[{"field": "role_ids", "message": "Несуществующая роль"}],
                    )
            await self._repo.set_roles(node_id, role_ids)
            node = await self._repo.set_visibility(
                node, visibility_mode="restricted", rag_exclude=payload.rag_exclude
            )
        else:
            await self._repo.set_roles(node_id, [])
            node = await self._repo.set_visibility(
                node, visibility_mode="inherit", rag_exclude=payload.rag_exclude
            )
        await self._repo.session.commit()
        logger.info("document_visibility_changed", node_id=str(node.id))
        return self._serialize(node, include_content=False)

    async def reorder(
        self, payload_parent_id: uuid.UUID | None, ids: list[uuid.UUID], *, scope: DocumentScope
    ) -> None:
        """Полная перестановка уровня. Прецеденция: 400 (форма) → 404 (id) → 422 (полнота)."""
        visible = await self._repo.list_visible_tree(scope)
        visible_ids = {n.id for n in visible}
        for node_id in ids:
            if node_id not in visible_ids:
                raise document_node_not_found()
        child_ids = {n.id for n in visible if _same_parent(n.parent_id, payload_parent_id)}
        if len(ids) != len(child_ids) or set(ids) != child_ids:
            raise unprocessable("Список не является полной перестановкой уровня")
        await self._repo.reorder(ids)
        await self._repo.session.commit()
        logger.info("documents_reordered", count=len(ids))

    # --- Копия / удаление -------------------------------------------------

    async def copy_node(
        self,
        node_id: uuid.UUID,
        payload: DocumentCopyRequest,
        *,
        scope: DocumentScope,
        owner_id: uuid.UUID,
    ) -> DocumentNodeResponse:
        """Рекурсивная копия поддерева (новые id, `content_version=1`, перенос видимости)."""
        source = await self._ensure_visible(scope, node_id)
        if "target_parent_id" in payload.model_fields_set:
            target_parent_id = payload.target_parent_id
        else:
            target_parent_id = source.parent_id

        if target_parent_id is not None:
            await self._resolve_parent(scope, target_parent_id)
            subtree = await self._repo.subtree_ids(node_id)
            if target_parent_id in subtree:
                raise document_copy_cycle()

        nodes = await self._repo.load_subtree(node_id)
        roles_map = await self._repo.roles_for_nodes([n.id for n in nodes])

        id_map: dict[uuid.UUID, uuid.UUID] = {}
        copies: list[DocumentNode] = []
        new_root: DocumentNode | None = None
        for original in nodes:
            if original.id == node_id:
                new_parent = target_parent_id
            else:
                # Не-корневой узел поддерева всегда имеет родителя внутри поддерева
                # (обход parent-before-child гарантирует наличие его копии в id_map).
                assert original.parent_id is not None
                new_parent = id_map[original.parent_id]
            copy = DocumentNode(
                node_type=original.node_type,
                parent_id=new_parent,
                name=original.name,
                content_md=original.content_md,
                owner_id=owner_id,
                visibility_mode=original.visibility_mode,
                position=original.position,
                content_version=1,
            )
            self._repo.add(copy)
            await self._repo.flush()
            id_map[original.id] = copy.id
            copies.append(copy)
            if original.id == node_id:
                new_root = copy

        role_rows: list[tuple[uuid.UUID, uuid.UUID]] = [
            (id_map[old_id], role_id)
            for old_id, role_ids in roles_map.items()
            for role_id in role_ids
        ]
        await self._repo.insert_roles(role_rows)
        assert new_root is not None  # поддерево всегда содержит корень (get_node дал 404 иначе)
        # Вложения копируются ФИЗИЧЕСКИ (новые id + новые файлы), ссылки в `content_md`
        # копии переписываются со старых id на новые — всё в этой же транзакции (ADR-068 §5).
        attachment_map = await self._attachments.copy_for_nodes(id_map, created_by=owner_id)
        if attachment_map and _rewrite_attachment_links(copies, attachment_map):
            await self._repo.flush()
            # ⚠️ Обязательный `refresh` после UPDATE-флеша (регресс `500` на копии документа
            # с вложением). `updated_at` вычисляет СЕРВЕР (`onupdate=func.now()`), и на
            # UPDATE значение инлайн не забирается (в отличие от INSERT, где сработал бы
            # `eager_defaults="auto"` через RETURNING) ⇒ атрибут остаётся unloaded. Тогда
            # `_serialize` ниже прочитал бы его ленивой догрузкой синхронным IO вне
            # greenlet → `MissingGreenlet` → `500`. `expire_on_commit=False` не спасает:
            # атрибут гасит именно flush UPDATE, а не commit.
            # Выбран явный `refresh` (а не `eager_defaults=True` на маппере): правка
            # локальна, не меняет форму SQL всех прочих UPDATE-ов `document_nodes` и
            # повторяет уже принятый в репозитории паттерн `flush` + `refresh`
            # (`apply_patch`/`set_visibility`). Достаточно корня — только он сериализуется.
            await self._repo.refresh(new_root)
        await self._repo.session.commit()
        logger.info("document_copied", source_id=str(node_id), new_id=str(new_root.id))
        return self._serialize(new_root, include_content=False)

    async def delete_node(self, node_id: uuid.UUID, *, scope: DocumentScope) -> None:
        """Soft-delete узла; папка — каскад поддерева (tombstone на каждый узел)."""
        await self._ensure_visible(scope, node_id)
        await self._repo.soft_delete_subtree(node_id)
        await self._repo.session.commit()
        logger.info("document_deleted", node_id=str(node_id))

    # --- role-refs --------------------------------------------------------

    async def list_role_refs(self) -> list[RoleRef]:
        """Роли для модалки видимости (`{id, name}`), сортировка по имени (ru, ci)."""
        roles = await self._roles.list_refs()
        roles_sorted = sorted(roles, key=lambda role: role.name.casefold())
        return [RoleRef(id=role.id, name=role.name) for role in roles_sorted]

    # --- Внешний read-only контур (RAG, X-API-Key, ADR-060) --------------

    async def list_external(
        self,
        *,
        updated_after: datetime | None,
        include_deleted: bool,
        cursor_token: str | None,
        limit: int,
    ) -> ExternalDocumentListResponse:
        """Страница внешнего синка (ADR-060 §3): keyset `(updated_at,id)` ASC, машина видит всё.

        Каждый элемент несёт ЭФФЕКТИВНЫЙ набор ролей (`visibility_role_ids`) + `content_version`
        (без `content_md`). Битый `cursor_token` → 400 validation_error.
        """
        cursor = self._decode_cursor(cursor_token)
        nodes = await self._repo.list_external(
            updated_after=updated_after,
            include_deleted=include_deleted,
            cursor=cursor,
            limit=limit + 1,
        )
        has_more = len(nodes) > limit
        page = nodes[:limit]
        # «Не включать в RAG»: эффективно исключённые узлы вырезаются ИЗ СТРАНИЦЫ (курсор
        # считается по неотфильтрованной странице — страница может быть короче limit, это
        # законно для keyset). Исключённый узел просто исчезает из полного листинга → RAG
        # снимает его с индексации своим механизмом «не встречен в обходе».
        rag_excluded = await self._repo.rag_excluded_ids_for([node.id for node in page])
        visible_page = [node for node in page if node.id not in rag_excluded]
        effective = await self._repo.effective_role_ids_for([node.id for node in visible_page])
        items = [
            self._external_node(node, effective.get(node.id, [])) for node in visible_page
        ]
        next_cursor = (
            encode_document_cursor(page[-1].updated_at, page[-1].id) if has_more and page else None
        )
        return ExternalDocumentListResponse(items=items, next_cursor=next_cursor)

    async def changes_external(
        self, *, since: datetime, cursor_token: str | None, limit: int
    ) -> ExternalDocumentListResponse:
        """Дельта с водяного знака `since` (ADR-060 §3): изменённые + tombstones (всегда).

        Tombstones включены безусловно (`include_deleted` подразумевается); в остальном —
        тот же keyset-контур, что `list_external`.
        """
        return await self.list_external(
            updated_after=since,
            include_deleted=True,
            cursor_token=cursor_token,
            limit=limit,
        )

    async def get_external(self, node_id: uuid.UUID) -> ExternalDocumentDetail:
        """Полный узел с контентом (ADR-060 §3). Удалён → 410 tombstone; не существовал → 404."""
        node = await self._repo.get_any(node_id)
        if node is None:
            raise document_node_not_found()
        if node.deleted_at is not None:
            raise document_node_gone(
                node_id=node.id,
                deleted_at=node.deleted_at,
                content_version=node.content_version,
            )
        _is_public, role_ids = await self._effective_role_ids(node_id)
        return ExternalDocumentDetail(
            id=node.id,
            node_type=node.node_type,
            parent_id=node.parent_id,
            name=node.name,
            visibility_role_ids=role_ids,
            content_version=node.content_version,
            updated_at=node.updated_at,
            deleted_at=node.deleted_at,
            content_md=node.content_md,
        )

    async def get_external_access(self, node_id: uuid.UUID) -> ExternalDocumentAccessResponse:
        """Эффективный уровень доступа узла (ADR-060 §2). Не существует/удалён → 404."""
        node = await self._repo.get_node(node_id)
        if node is None:
            raise document_node_not_found()
        is_public, role_ids = await self._effective_role_ids(node_id)
        return ExternalDocumentAccessResponse(
            id=node.id,
            is_public=is_public,
            visibility_role_ids=role_ids,
            content_version=node.content_version,
        )

    def _decode_cursor(self, cursor_token: str | None) -> tuple[datetime, uuid.UUID] | None:
        """Декодирует opaque keyset-курсор; отсутствие → None; битый → 400 validation_error."""
        if not cursor_token:
            return None
        try:
            return decode_document_cursor(cursor_token)
        except DocumentCursorError as exc:
            raise validation_error("Битый курсор пагинации") from exc

    async def _effective_role_ids(self, node_id: uuid.UUID) -> tuple[bool, list[uuid.UUID]]:
        """`(is_public, эффективный набор ролей)` одного узла (ADR-060 §2).

        Публичен (нет `restricted`-предка до корня) → `(True, [])`; иначе `(False, роли
        управляющего restricted-узла)` в детерминированном порядке.
        """
        governing = await self._repo.governing_restricted(node_id)
        if governing is None:
            return True, []
        role_ids = await self._repo.node_role_ids(governing)
        return False, sorted(role_ids, key=str)

    @staticmethod
    def _external_node(node: DocumentNode, role_ids: list[uuid.UUID]) -> ExternalDocumentNode:
        """Метаданные узла для внешних списков/дельты (без `content_md`)."""
        return ExternalDocumentNode(
            id=node.id,
            node_type=node.node_type,
            parent_id=node.parent_id,
            name=node.name,
            visibility_role_ids=role_ids,
            content_version=node.content_version,
            updated_at=node.updated_at,
            deleted_at=node.deleted_at,
        )

    # --- Внутренние помощники --------------------------------------------

    async def _ensure_visible(self, scope: DocumentScope, node_id: uuid.UUID) -> DocumentNode:
        """Загружает узел и проверяет видимость по роли. Невидим/нет → 404.

        Само правило живёт в `document_visibility` — единый источник и для вложений
        (ADR-068: «доступ к картинке = доступ к её узлу»).
        """
        return await ensure_visible_node(self._repo, scope, node_id)

    async def _resolve_parent(self, scope: DocumentScope, parent_id: uuid.UUID | None) -> None:
        """Проверяет родителя: null допустим; иначе — видимый узел-папка (404/400)."""
        if parent_id is None:
            return
        parent = await self._ensure_visible(scope, parent_id)
        if parent.node_type != "folder":
            raise validation_error(
                "Родитель не является папкой",
                details=[{"field": "parent_id", "message": "Родитель не является папкой"}],
            )

    def _ensure_md_size(self, content: str) -> None:
        """Проверяет размер markdown в байтах (UTF-8) ≤ лимита; иначе 422 validation_error."""
        if len(content.encode("utf-8")) > self._settings.documents_max_md_bytes:
            raise _content_md_error("Превышен лимит размера содержимого документа")

    @staticmethod
    def _serialize(node: DocumentNode, *, include_content: bool) -> DocumentNodeResponse:
        return DocumentNodeResponse(
            id=node.id,
            node_type=node.node_type,
            parent_id=node.parent_id,
            name=node.name,
            content_md=node.content_md if include_content else None,
            owner_id=node.owner_id,
            visibility_mode=node.visibility_mode,
            content_version=node.content_version,
            position=node.position,
            created_at=node.created_at,
            updated_at=node.updated_at,
        )


def _rewrite_attachment_links(
    copies: list[DocumentNode], attachment_map: dict[uuid.UUID, uuid.UUID]
) -> bool:
    """Литеральная замена `/api/documents/attachments/<old>` → `…/<new>` в копиях (ADR-068 §5).

    **Regex-разбор markdown запрещён** (ложные срабатывания в коде/цитатах) — только
    подстановка по точной подстроке для каждой пары карты копирования. Коллизий не бывает:
    новые `id` — свежие UUID, они не могут совпасть со старыми.

    Возвращает `True`, если хоть один `content_md` действительно изменился. Атрибут
    присваивается ТОЛЬКО при фактическом изменении: иначе unit-of-work сгенерировал бы
    холостой `UPDATE` (а с ним — гашение серверного `updated_at`) там, где ссылок на
    вложения в тексте нет вовсе.
    """
    replacements = [
        (f"{ATTACHMENT_URL_PREFIX}{old_id}", f"{ATTACHMENT_URL_PREFIX}{new_id}")
        for old_id, new_id in attachment_map.items()
    ]
    changed = False
    for copy in copies:
        content = copy.content_md
        if not content:
            continue
        for old_url, new_url in replacements:
            content = content.replace(old_url, new_url)
        if content != copy.content_md:
            copy.content_md = content
            changed = True
    return changed


def _same_parent(node_parent: uuid.UUID | None, level_parent: uuid.UUID | None) -> bool:
    """Узел принадлежит уровню `level_parent` (корректная сверка с учётом NULL-корня)."""
    return node_parent == level_parent
