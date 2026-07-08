"""Модель таблицы `teams` и M2M `user_teams` (03-data-model.md, ADR-022).

**CRM-команды** — группировка пользователей вокруг лидера. Не путать с mail-«командами»
(`groups` внешнего сервиса, `GET /api/mail/teams`) — это отдельная сущность в БД CRM
(uuid, лидер+участники). `user_teams` — первая M2M-таблица в проекте.

Инвариант «если лидер задан — он ∈ участники» обеспечивает сервис (единственная точка
записи); БД его не форсирует. **Лидер опционален** (`leader_id` nullable, ADR-026):
`teams.leader_id` → users(id) ON DELETE SET NULL (удаление пользователя-лидера НЕ
блокируется; осмысленного лидера проставляет авто-передача в сервисе). `user_teams` —
обе стороны ON DELETE CASCADE, с `created_at` (дата добавления → порядок авто-передачи).
Отношения `leader`/`members` объявлены `viewonly` (членство пишется явными statements в
репозитории для контроля транзакции и инварианта лидера).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Table,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# Ассоциативная таблица M2M users↔teams (составной PK, обе FK ON DELETE CASCADE).
# `created_at` (ADR-026, миграция 0012) — дата добавления участника: определяет порядок
# авто-назначения/авто-передачи лидерства («первый/следующий по дате»).
user_teams = Table(
    "user_teams",
    Base.metadata,
    Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_user_teams_user_id"),
        primary_key=True,
    ),
    Column(
        "team_id",
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE", name="fk_user_teams_team_id"),
        primary_key=True,
    ),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    ),
)


class Team(Base):
    """Реестр CRM-команд (лидер + участники, ADR-022)."""

    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 64 "
            "AND name = btrim(name) "
            "AND name !~ '[[:cntrl:]]'",
            name="ck_teams_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # NULL = команда без лидера (ADR-026, миграция 0012). FK ON DELETE SET NULL —
    # предохранитель: удаление пользователя-лидера не блокируется (осмысленного
    # нового лидера проставляет авто-передача в сервисе). Инвариант «если лидер
    # задан — он ∈ участники» обеспечивает сервис.
    leader_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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

    # Лидер — many-to-one по leader_id (foreign_keys дизамбигуирует vs secondary).
    # Опционален (leader_id nullable) — при NULL relationship отдаёт None.
    leader: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[leader_id],
        viewonly=True,
        lazy="select",
    )
    # Участники — M2M через user_teams (включая лидера, гарантирует сервис). Порядок —
    # по дате добавления (`user_teams.created_at`), совпадает с порядком авто-передачи.
    members: Mapped[list[User]] = relationship(
        "User",
        secondary=user_teams,
        viewonly=True,
        lazy="select",
        order_by=user_teams.c.created_at,
    )
