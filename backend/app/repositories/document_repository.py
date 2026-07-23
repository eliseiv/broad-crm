"""Репозиторий модуля «Документы» (SQLAlchemy 2.0 async, ADR-059).

Доступ к `document_nodes`/`document_node_roles`: CRUD узлов, листинг уровня/дерева под
per-node фильтром видимости (рекурсивные CTE), обход поддерева (копия/каскадный
soft-delete), проверка цикла copy. Только `flush` — `commit` выполняет сервис.

Все внутренние выборки несут `WHERE deleted_at IS NULL` (03-data-model.md: удалённый
узел исключён из всех внутренних выборок).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, bindparam, delete, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.documents import DocumentScope
from app.models.document_node import DocumentNode
from app.models.document_node_role import document_node_roles

# Порядок уровня — канон `position` (03-data-model.md#колонка-position, 04-api.md#documents).
_ORDER_BY = "ORDER BY dn.position ASC, dn.created_at DESC, dn.id ASC"

# Top-down рекурсивный CTE: пропагирует «управляющий» (`gov`) ближайший `restricted`-предок
# (включая сам узел) вниз по дереву. Узел виден ⇔ `gov IS NULL` (публичен — нет
# `restricted`-предка до корня) ИЛИ роль пользователя ∈ набор ролей узла `gov`.
_VISIBLE_CTE = """
WITH RECURSIVE tree AS (
    SELECT n.id AS id, n.parent_id AS parent_id, n.visibility_mode AS visibility_mode,
           CASE WHEN n.visibility_mode = 'restricted' THEN n.id ELSE NULL END AS gov
    FROM document_nodes n
    WHERE n.parent_id IS NULL AND n.deleted_at IS NULL
  UNION ALL
    SELECT c.id, c.parent_id, c.visibility_mode,
           CASE WHEN c.visibility_mode = 'restricted' THEN c.id ELSE t.gov END
    FROM document_nodes c
    JOIN tree t ON c.parent_id = t.id
    WHERE c.deleted_at IS NULL
)
SELECT dn.* FROM document_nodes dn
JOIN tree t ON t.id = dn.id
WHERE ({visibility_clause})
{parent_clause}
{order_by}
"""

# Предикат видимости узла. Публичен (`gov IS NULL`) — виден всегда; иначе роль
# пользователя должна входить в набор ролей управляющего `restricted`-узла.
_VISIBLE_WITH_ROLE = (
    "t.gov IS NULL OR EXISTS ("
    "SELECT 1 FROM document_node_roles r "
    "WHERE r.node_id = t.gov AND r.role_id = CAST(:role_id AS uuid))"
)
# `role_id is None` (нет роли — напр. будущий не-admin актор без строки в `users`):
# симметрично `_ensure_visible` виден ТОЛЬКО публичный узел. Раньше сюда уходила пустая
# строка в `CAST('' AS uuid)` → `invalid input syntax for type uuid` (500); теперь путь
# без `:role_id` вовсе (bindparam не передаётся).
_VISIBLE_PUBLIC_ONLY = "t.gov IS NULL"

# Ближайший `restricted`-предок узла (03-data-model.md#резолюция-эффективной-видимости).
_GOVERNING_SQL = text(
    """
WITH RECURSIVE chain AS (
    SELECT id, parent_id, visibility_mode, 0 AS depth
    FROM document_nodes WHERE id = CAST(:node_id AS uuid) AND deleted_at IS NULL
  UNION ALL
    SELECT n.id, n.parent_id, n.visibility_mode, c.depth + 1
    FROM document_nodes n JOIN chain c ON n.id = c.parent_id
    WHERE n.deleted_at IS NULL
)
SELECT id FROM chain WHERE visibility_mode = 'restricted' ORDER BY depth ASC LIMIT 1
"""
)

# id всех узлов поддерева (включая корень) — для cycle-check copy и каскадного soft-delete.
_SUBTREE_IDS_SQL = text(
    """
WITH RECURSIVE sub AS (
    SELECT id FROM document_nodes WHERE id = CAST(:root_id AS uuid) AND deleted_at IS NULL
  UNION ALL
    SELECT n.id FROM document_nodes n JOIN sub s ON n.parent_id = s.id
    WHERE n.deleted_at IS NULL
)
SELECT id FROM sub
"""
)

# Полные узлы поддерева, упорядоченные parent-before-child (для рекурсивной копии).
_SUBTREE_NODES_SQL = """
WITH RECURSIVE sub AS (
    SELECT dn.*, 0 AS depth FROM document_nodes dn
    WHERE dn.id = CAST(:root_id AS uuid) AND dn.deleted_at IS NULL
  UNION ALL
    SELECT dn.*, s.depth + 1 FROM document_nodes dn
    JOIN sub s ON dn.parent_id = s.id
    WHERE dn.deleted_at IS NULL
)
SELECT * FROM sub ORDER BY depth ASC, position ASC, created_at DESC, id ASC
"""

# Ближайший `restricted`-предок (включая сам узел) для НАБОРА узлов сразу — bulk-резолюция
# эффективной видимости внешнего контура (ADR-060 §2) без N+1. Восходящий обход по
# `parent_id` от каждого якоря; `DISTINCT ON (anchor) ... ORDER BY depth ASC` берёт
# ближайший. Якорь без `restricted`-предка в результат не попадает ⇒ публичен (роли `[]`).
# Обход НЕ фильтрует `deleted_at`: у живого узла все предки живы (каскадный soft-delete
# удаляет поддерево целиком), а для tombstone восстанавливается его СТРУКТУРНЫЙ набор.
_EFFECTIVE_GOV_SQL = text(
    """
WITH RECURSIVE up AS (
    SELECT n.id AS anchor, n.id AS node_id, n.parent_id AS parent_id,
           n.visibility_mode AS visibility_mode, 0 AS depth
    FROM document_nodes n
    WHERE n.id IN :ids
  UNION ALL
    SELECT u.anchor, p.id, p.parent_id, p.visibility_mode, u.depth + 1
    FROM document_nodes p JOIN up u ON p.id = u.parent_id
)
SELECT DISTINCT ON (anchor) anchor, node_id AS gov
FROM up
WHERE visibility_mode = 'restricted'
ORDER BY anchor, depth ASC
"""
)


# Эффективное исключение из RAG: узел исключён, если `rag_exclude=true` у него самого или
# ЛЮБОГО предка (наследование вниз по дереву, симметрично видимости). Восходящий bulk-CTE
# по образцу _EFFECTIVE_GOV_SQL; deleted_at не фильтруется по той же причине.
_RAG_EXCLUDED_SQL = text(
    """
WITH RECURSIVE up AS (
    SELECT n.id AS anchor, n.parent_id AS parent_id, n.rag_exclude AS rag_exclude
    FROM document_nodes n
    WHERE n.id IN :ids
  UNION ALL
    SELECT u.anchor, p.parent_id, p.rag_exclude
    FROM document_nodes p JOIN up u ON p.id = u.parent_id
)
SELECT DISTINCT anchor FROM up WHERE rag_exclude
"""
)


class DocumentRepository:
    """CRUD и резолюция видимости узлов дерева документов."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    # --- Точечные выборки -------------------------------------------------

    async def get_node(self, node_id: uuid.UUID) -> DocumentNode | None:
        """Не удалённый узел по id (или None). Удалённый узел не отдаётся."""
        stmt = select(DocumentNode).where(
            DocumentNode.id == node_id, DocumentNode.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def governing_restricted(self, node_id: uuid.UUID) -> uuid.UUID | None:
        """Ближайший `restricted`-предок узла (включая сам узел) или None (публичен)."""
        result = await self._session.execute(_GOVERNING_SQL.bindparams(node_id=str(node_id)))
        return result.scalar_one_or_none()

    async def node_role_ids(self, node_id: uuid.UUID) -> set[uuid.UUID]:
        """Набор `role_id`, привязанных к узлу (строки `document_node_roles`)."""
        stmt = select(document_node_roles.c.role_id).where(document_node_roles.c.node_id == node_id)
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    # --- Листинги под фильтром видимости ----------------------------------

    async def list_visible_tree(self, scope: DocumentScope) -> list[DocumentNode]:
        """Все видимые не удалённые узлы (для `GET /tree`), в каноничном порядке уровня."""
        return await self._list_visible(scope, parent_filter=False, parent_id=None)

    async def list_visible_children(
        self, scope: DocumentScope, parent_id: uuid.UUID | None
    ) -> list[DocumentNode]:
        """Видимые не удалённые дети уровня `parent_id` (None = корень) — `GET /nodes`."""
        return await self._list_visible(scope, parent_filter=True, parent_id=parent_id)

    async def _list_visible(
        self,
        scope: DocumentScope,
        *,
        parent_filter: bool,
        parent_id: uuid.UUID | None,
    ) -> list[DocumentNode]:
        if scope.sees_all:
            stmt = select(DocumentNode).where(DocumentNode.deleted_at.is_(None))
            if parent_filter:
                stmt = stmt.where(
                    DocumentNode.parent_id.is_(None)
                    if parent_id is None
                    else DocumentNode.parent_id == parent_id
                )
            stmt = stmt.order_by(
                DocumentNode.position.asc(),
                DocumentNode.created_at.desc(),
                DocumentNode.id.asc(),
            )
            result = await self._session.execute(stmt)
            return list(result.scalars().all())

        parent_clause = ""
        params: dict[str, str] = {}
        if scope.role_id is not None:
            visibility_clause = _VISIBLE_WITH_ROLE
            params["role_id"] = str(scope.role_id)
        else:
            visibility_clause = _VISIBLE_PUBLIC_ONLY
        if parent_filter:
            if parent_id is None:
                parent_clause = "AND dn.parent_id IS NULL"
            else:
                parent_clause = "AND dn.parent_id = CAST(:parent_id AS uuid)"
                params["parent_id"] = str(parent_id)
        sql = _VISIBLE_CTE.format(
            visibility_clause=visibility_clause,
            parent_clause=parent_clause,
            order_by=_ORDER_BY,
        )
        orm_stmt = select(DocumentNode).from_statement(text(sql).bindparams(**params))
        result = await self._session.execute(orm_stmt)
        return list(result.scalars().all())

    # --- Обход поддерева --------------------------------------------------

    async def subtree_ids(self, root_id: uuid.UUID) -> set[uuid.UUID]:
        """id всех не удалённых узлов поддерева (включая корень) — cycle-check/каскад."""
        result = await self._session.execute(_SUBTREE_IDS_SQL.bindparams(root_id=str(root_id)))
        return set(result.scalars().all())

    async def load_subtree(self, root_id: uuid.UUID) -> list[DocumentNode]:
        """Узлы поддерева (parent-before-child) для рекурсивной копии."""
        stmt = select(DocumentNode).from_statement(
            text(_SUBTREE_NODES_SQL).bindparams(root_id=str(root_id))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def roles_for_nodes(self, node_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
        """`{node_id: [role_id, ...]}` для узлов поддерева (перенос видимости при копии)."""
        if not node_ids:
            return {}
        stmt = select(document_node_roles.c.node_id, document_node_roles.c.role_id).where(
            document_node_roles.c.node_id.in_(node_ids)
        )
        result = await self._session.execute(stmt)
        mapping: dict[uuid.UUID, list[uuid.UUID]] = {}
        for node_id, role_id in result.all():
            mapping.setdefault(node_id, []).append(role_id)
        return mapping

    async def soft_delete_subtree(self, root_id: uuid.UUID) -> None:
        """Каскадный soft-delete всего поддерева (tombstone на каждый узел) в одной транзакции.

        `content_version` НЕ меняется (03-data-model.md); `updated_at` обновляется —
        водяной знак внешнего sync. Только ещё не удалённые узлы.
        """
        ids = await self.subtree_ids(root_id)
        if not ids:
            return
        now = func.now()
        stmt = (
            update(DocumentNode)
            .where(DocumentNode.id.in_(ids), DocumentNode.deleted_at.is_(None))
            .values(deleted_at=now, updated_at=now)
        )
        await self._session.execute(stmt)

    # --- Внешний read-only контур (RAG, ADR-060) -------------------------

    async def get_any(self, node_id: uuid.UUID) -> DocumentNode | None:
        """Узел по id ВКЛЮЧАЯ удалённый (внешний `GET /{id}`: tombstone → 410).

        В отличие от `get_node` НЕ фильтрует `deleted_at`: удалённый узел нужен, чтобы
        отдать tombstone. `None` — узла никогда не существовало (→ 404).
        """
        stmt = select(DocumentNode).where(DocumentNode.id == node_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_external(
        self,
        *,
        updated_after: datetime | None,
        include_deleted: bool,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[DocumentNode]:
        """Keyset-листинг ВСЕХ узлов для внешнего синка (ADR-060 §3, порядок `(updated_at,id)` ASC).

        Машина видит все узлы (per-role фильтр не применяется). `include_deleted=False` →
        только живые (`deleted_at IS NULL`); `True` → и tombstones. `updated_after` — фильтр
        `updated_at >= updated_after` (водяной знак). `cursor` — позиция `(updated_at, id)`
        для предиката `(updated_at, id) > (u0, id0)`. Вызывающий передаёт `limit + 1` для
        детекции следующей страницы.
        """
        stmt = select(DocumentNode)
        if not include_deleted:
            stmt = stmt.where(DocumentNode.deleted_at.is_(None))
        if updated_after is not None:
            stmt = stmt.where(DocumentNode.updated_at >= updated_after)
        if cursor is not None:
            u0, id0 = cursor
            stmt = stmt.where(
                or_(
                    DocumentNode.updated_at > u0,
                    and_(DocumentNode.updated_at == u0, DocumentNode.id > id0),
                )
            )
        stmt = stmt.order_by(DocumentNode.updated_at.asc(), DocumentNode.id.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def effective_role_ids_for(
        self, node_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        """`{node_id: эффективный набор ролей}` для набора узлов (ADR-060 §2), пусто = публичен.

        Bulk-резолюция без N+1: один восходящий CTE находит управляющий `restricted`-узел
        каждого якоря, затем один запрос ролей по множеству управляющих. Публичный узел
        (нет `restricted`-предка) → `[]`. Порядок ролей детерминирован (`sorted`).
        """
        if not node_ids:
            return {}
        stmt = _EFFECTIVE_GOV_SQL.bindparams(bindparam("ids", value=node_ids, expanding=True))
        rows = (await self._session.execute(stmt)).all()
        gov_by_anchor: dict[uuid.UUID, uuid.UUID] = {row[0]: row[1] for row in rows}
        roles_by_gov = await self.roles_for_nodes(list(set(gov_by_anchor.values())))
        result: dict[uuid.UUID, list[uuid.UUID]] = {}
        for node_id in node_ids:
            gov = gov_by_anchor.get(node_id)
            result[node_id] = sorted(roles_by_gov.get(gov, []), key=str) if gov is not None else []
        return result

    async def rag_excluded_ids_for(self, node_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Подмножество `node_ids`, ЭФФЕКТИВНО исключённых из RAG (флаг на узле/предке)."""
        if not node_ids:
            return set()
        stmt = _RAG_EXCLUDED_SQL.bindparams(bindparam("ids", value=node_ids, expanding=True))
        rows = (await self._session.execute(stmt)).all()
        return {row[0] for row in rows}

    # --- Мутации ----------------------------------------------------------

    async def create(
        self,
        *,
        node_type: str,
        parent_id: uuid.UUID | None,
        name: str,
        content_md: str | None,
        owner_id: uuid.UUID,
    ) -> DocumentNode:
        """Создаёт узел (`visibility_mode='inherit'`, `content_version=1`, `position=0`)."""
        node = DocumentNode(
            node_type=node_type,
            parent_id=parent_id,
            name=name,
            content_md=content_md,
            owner_id=owner_id,
        )
        self._session.add(node)
        await self._session.flush()
        await self._session.refresh(node)
        return node

    def add(self, node: DocumentNode) -> None:
        """Регистрирует новый узел в сессии (bulk-копия; flush выполняет сервис)."""
        self._session.add(node)

    async def flush(self) -> None:
        """Сброс pending-изменений в БД без commit (id новых узлов становятся доступны)."""
        await self._session.flush()

    async def refresh(self, node: DocumentNode) -> None:
        """Перечитывает узел из БД (значения, вычисленные СЕРВЕРОМ, становятся загруженными).

        Нужен после `flush` UPDATE-а: `updated_at` (`onupdate=func.now()`) считает сервер и
        инлайн он не возвращается ⇒ атрибут остаётся unloaded, а его чтение в async-контексте
        вне greenlet упало бы `MissingGreenlet`. Тот же приём, что в `apply_patch`.
        """
        await self._session.refresh(node)

    async def set_roles(self, node_id: uuid.UUID, role_ids: list[uuid.UUID]) -> None:
        """Перезаписывает набор ролей узла (`restricted`): удаляет старые, вставляет новые."""
        await self._session.execute(
            delete(document_node_roles).where(document_node_roles.c.node_id == node_id)
        )
        if role_ids:
            await self._session.execute(
                insert(document_node_roles),
                [{"node_id": node_id, "role_id": rid} for rid in role_ids],
            )

    async def insert_roles(self, rows: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        """Bulk-вставка строк `(node_id, role_id)` (перенос видимости при копии)."""
        if not rows:
            return
        await self._session.execute(
            insert(document_node_roles),
            [{"node_id": nid, "role_id": rid} for nid, rid in rows],
        )

    async def apply_patch(
        self,
        node: DocumentNode,
        *,
        name: str | None,
        content_md: str | None,
        set_content: bool,
        bump_version: bool,
    ) -> DocumentNode:
        """Применяет rename/content к загруженному узлу; `content_version += 1` при правке.

        `set_content` — было ли поле `content_md` передано (позволяет отличить «не менять»
        от «очистить в пусто»). `updated_at` обновляется через `onupdate` при flush.
        """
        if name is not None:
            node.name = name
        if set_content:
            node.content_md = content_md
        if bump_version:
            node.content_version = node.content_version + 1
        await self._session.flush()
        await self._session.refresh(node)
        return node

    async def set_visibility(
        self, node: DocumentNode, *, visibility_mode: str, rag_exclude: bool
    ) -> DocumentNode:
        """Меняет `visibility_mode`/`rag_exclude` узла (`content_version` НЕ трогается; `updated_at` — да)."""
        node.visibility_mode = visibility_mode
        node.rag_exclude = rag_exclude
        await self._session.flush()
        await self._session.refresh(node)
        return node

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        """Присваивает `position = 0..N-1` по индексу (одна транзакция; commit — сервис).

        `updated_at` обновляется (перемещение — мутация, водяной знак sync); валидация
        полноты перестановки — в сервисе (04-api.md#прецеденция-ошибок-валидации).
        """
        now = func.now()
        for index, node_id in enumerate(ordered_ids):
            await self._session.execute(
                update(DocumentNode)
                .where(DocumentNode.id == node_id)
                .values(position=index, updated_at=now)
            )
