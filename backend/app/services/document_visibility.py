"""Per-node резолюция видимости узла документов (ADR-059, 05-security.md).

Единственный источник правила для обоих потребителей — `DocumentService` (узлы) и
`DocumentAttachmentService` (вложения, ADR-068): «доступ к картинке = доступ к её узлу».
Вынесено сюда, чтобы правило не дублировалось в двух сервисах и чтобы между ними не
возникла циклическая зависимость.
"""

from __future__ import annotations

import uuid

from app.domain.documents import DocumentScope
from app.errors import document_node_not_found
from app.models.document_node import DocumentNode
from app.repositories.document_repository import DocumentRepository


async def resolve_visible_node(
    repo: DocumentRepository, scope: DocumentScope, node_id: uuid.UUID
) -> DocumentNode | None:
    """Узел, если он существует, не soft-deleted и виден в `scope`; иначе `None`.

    Admin-уровень (`sees_all`) видит всё. Иначе узел виден ⇔ он публичен внутри модуля
    (нет `restricted`-предка до корня) ИЛИ роль пользователя входит в набор ролей
    управляющего `restricted`-узла.
    """
    node = await repo.get_node(node_id)
    if node is None:
        return None
    if scope.sees_all:
        return node
    governing = await repo.governing_restricted(node_id)
    if governing is None:
        return node
    role_ids = await repo.node_role_ids(governing)
    if scope.role_id is not None and scope.role_id in role_ids:
        return node
    return None


async def ensure_visible_node(
    repo: DocumentRepository, scope: DocumentScope, node_id: uuid.UUID
) -> DocumentNode:
    """То же, но невидимый/несуществующий узел → `404 document_node_not_found`.

    Анти-энумерация: невидимое неотличимо от несуществующего (НЕ 403).
    """
    node = await resolve_visible_node(repo, scope, node_id)
    if node is None:
        raise document_node_not_found()
    return node
