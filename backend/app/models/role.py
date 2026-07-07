"""Модель таблицы `roles` (03-data-model.md#таблицы-roles-и-users-rbac, ADR-021).

Роль с правами-матрицей (`permissions`, jsonb). `name` уникален (дубль →
409 role_name_taken). `admin` — зарезервированное имя (гейт страницы «Пользователи»,
сидится миграцией 0008). Права валидируются против каталога на уровне приложения
(app/domain/permissions.py), не в БД (по образцу «свободного» инварианта).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class Role(Base):
    """Реестр ролей RBAC. `permissions` — `{page: [action, ...]}` (каталог ADR-021)."""

    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="ck_roles_name_len"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    permissions: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    users: Mapped[list[User]] = relationship(back_populates="role")
