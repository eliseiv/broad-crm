"""Таблица `document_node_roles` — видимость узел ↔ роль (03-data-model.md, ADR-059).

Набор ролей, которым виден `restricted`-узел. Образец связки — `user_channel_teams`:
composite PK, обе FK `ON DELETE CASCADE`, индекс на «правую» колонку (`role_id`). Строк
НЕТ для узлов `visibility_mode='inherit'` (их видимость наследуется/публична).

`ix_document_node_roles_role_id` обязателен: под `ON DELETE CASCADE` при удалении роли
(иначе seq-scan) и под обратную выборку «какие узлы видит роль».
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    ForeignKey,
    Index,
    Table,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base

document_node_roles = Table(
    "document_node_roles",
    Base.metadata,
    Column(
        "node_id",
        UUID(as_uuid=True),
        ForeignKey(
            "document_nodes.id",
            ondelete="CASCADE",
            name="fk_document_node_roles_node_id",
        ),
        primary_key=True,
    ),
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE", name="fk_document_node_roles_role_id"),
        primary_key=True,
    ),
    Index("ix_document_node_roles_role_id", "role_id"),
)
