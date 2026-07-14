"""Модель таблицы `users` (03-data-model.md#таблицы-roles-и-users-rbac, ADR-021).

Дополнительные (БД) пользователи многопользовательского режима. Супер-админ (`.env`)
БД-пользователем НЕ является; в `users` у него есть лишь **системная строка-якорь**
(`is_system=true`, ADR-051) — FK-цель личного состояния, невидимая для реестра, логина
и Telegram-SSO. `username` допускает кириллицу/юникод-буквы (DB-CHECK — «свободный»
инвариант; полное правило — Pydantic/app.domain.identity). Пароль — только bcrypt-хэш
(`password_hash`), plaintext не хранится. `role_id` FK → roles ON DELETE RESTRICT
(роль с носителями удалить нельзя → 409 role_in_use). Роль подгружается eager
(`lazy="joined"`) — безопасно в async (загрузка в том же SELECT, без ленивого IO).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.role import Role
    from app.models.team import Team


class User(Base):
    """Реестр БД-пользователей. Пароль — только `password_hash` (bcrypt, ADR-021)."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "char_length(username) BETWEEN 1 AND 64 "
            "AND username = btrim(username) "
            "AND username !~ '[[:cntrl:]]'",
            name="ck_users_username",
        ),
        # Системная строка-якорь супер-админа — ровно ОДНА (ADR-051 §1.1). Индекс
        # объявлен в МОДЕЛИ, а миграция 0026 его зеркалит: иначе схема тестов
        # (`Base.metadata.create_all`) разошлась бы с прод-схемой, и регрессия,
        # создающая вторую системную строку, прошла бы зелёные тесты.
        Index(
            "uq_users_system_singleton",
            "is_system",
            unique=True,
            postgresql_where=text("is_system"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Опциональный телеграм-ник (ADR-025; заменяет прежний email из ADR-022). Уникален
    # только среди заданных (частичный уникальный индекс uq_users_telegram
    # WHERE telegram IS NOT NULL, миграция 0011). Хранится нормализованным (без `@`,
    # lower-case); формат — на Pydantic/домене (app.domain.telegram).
    telegram: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL = беспарольный пользователь (пароль ещё не задан — «открытый первый вход»,
    # ADR-025, миграция 0011). Непустой — bcrypt-хэш. Plaintext не хранится.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Системная строка-якорь супер-админа (ADR-051, миграция 0026). `true` — ровно одна
    # техническая строка (FK-цель личного состояния консольного супер-админа); `false` —
    # обычный пользователь. Наружу НЕ отдаётся ни в одном контракте. Строки с
    # `is_system=true` невидимы для методов-резолверов `UserRepository` (реестр, логин,
    # Telegram-SSO, валидация ссылок) — правило 03-data-model.md#системная-строка-якорь.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Момент ПЕРВОГО успешного входа (ADR-028, миграция 0015). NULL = ещё ни разу не
    # входил. Проставляется идемпотентно (`if None`) в парольной ветке login и в
    # set-password. Наружу не отдаётся — источник производного UserListItem.status.
    first_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Флаги «Без команды» по каналам (ADR-055 §2.2, миграция 0027). `true` — пользователь
    # видит И правит объекты канала с `team_id IS NULL` (ящики/номера без команды) наравне
    # со своей командой. НЕ команда, а отдельное измерение scope ⇒ булева колонка, а не
    # строка в `user_channel_teams` (NULL в PK PostgreSQL недопустим — §2.2).
    # ⚠️ Флаг НЕ даёт права СОЗДАВАТЬ ящик с `team_id=null` и переносить ящик между
    # командами — это по-прежнему admin-уровень (ADR-044 §4 не разворачивается).
    mail_includes_unassigned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    sms_includes_unassigned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
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

    role: Mapped[Role] = relationship(back_populates="users", lazy="joined")
    # CRM-команды пользователя (M2M через user_teams). `viewonly` — членство пишется
    # явными statements в репозитории. Грузится точечно через selectinload (список/
    # деталь пользователя); в hot-path принципала (get_by_id) не загружается.
    teams: Mapped[list[Team]] = relationship(
        "Team",
        secondary="user_teams",
        viewonly=True,
        lazy="select",
        order_by="Team.created_at.desc()",
    )
