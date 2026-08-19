"""Таблица `user_roles` — роли пользователя (M2M, 03-data-model.md, ADR-079 §1).

Образец связки — `document_node_role.py`: `Table` + composite PK + индекс на «правую»
колонку (`role_id`). Отличие принципиальное: **FK ролевой стороны — `ON DELETE
RESTRICT`**, а не `CASCADE`. Это зеркало сегодняшнего `409 role_in_use`: `CASCADE` молча
снял бы роль со всех её носителей при удалении роли.

`created_at` задаёт **порядок ролей** пользователя (первая роль = информационный
JWT-claim `role` и `role_id`/`role_name` внешнего контура бота, ADR-079 §3/§6).

`ix_user_roles_role_id` обязателен: под гард `DELETE /api/roles/{id}` (иначе seq-scan) и
под обратную выборку «кто в роли».
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Table,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_roles_user_id"),
        primary_key=True,
    ),
    Column(
        "role_id",
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT", name="fk_user_roles_role_id"),
        primary_key=True,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Index("ix_user_roles_role_id", "role_id"),
)
