"""Инфраструктура integration-тестов модуля «Документы» (реальный Postgres, ADR-059/060).

Вспомогательный модуль (без `test_`-префикса — pytest его не коллектит). Даёт:
- `documents_db()` — async-engine + sessionmaker поверх реального Postgres (create_all +
  TRUNCATE на входе для изоляции; семантика как app/db.py: expire_on_commit=False);
- `build_app()` / `client()` — переиспользуются из `sms_helpers` (канало-агностичны);
- `build_principal()` — принципал С `role_id` (per-node фильтр видимости документов);
- seed-хелперы (роль/пользователь/узел/строки видимости).

Реальный URL БД захватывается на импорте (до autouse-фикстуры conftest, monkeypatch'ащей
DATABASE_URL на фейковый). CI задаёт DATABASE_URL (postgres:16); локально — TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.models import Base
from app.models.document_node import DocumentNode
from app.models.document_node_role import document_node_roles
from app.models.role import Role
from app.models.team import Team, user_teams
from app.models.user import User

# Переиспускаем канало-агностичные хелперы приложения/клиента.
from sms_helpers import build_app, client  # noqa: F401  (реэкспорт для тестов)
from sqlalchemy import insert
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

# Очищаемые между тестами таблицы. `document_node_roles`/`document_nodes` — данные модуля;
# roles/users/teams — их FK-цели (owner_id → users, role_id → roles).
_TRUNCATE = "document_node_roles, document_nodes, user_teams, teams, users, roles"


@asynccontextmanager
async def documents_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessionmaker поверх реального Postgres; чистая схема + TRUNCATE для изоляции."""
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — integration-тесты документов требуют "
            "реального Postgres (CI поднимает postgres:16; локально — контейнер)."
        )
    engine = create_async_engine(_DB_URL, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(sa_text(f"TRUNCATE {_TRUNCATE} RESTART IDENTITY CASCADE"))
        sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _bootstrap_superadmin_anchor(sm)
        yield sm
    finally:
        await engine.dispose()


async def _bootstrap_superadmin_anchor(sm: async_sessionmaker[AsyncSession]) -> None:
    """Сеет строку-якорь супер-админа (ADR-051 §1.3) — источник `owner_id` по умолчанию."""
    from app.repositories.user_repository import UserRepository

    async with sm() as session:
        await UserRepository(session).ensure_superadmin_anchor()


def build_principal(
    *,
    user_id: uuid.UUID | None = None,
    is_superadmin: bool = True,
    role: str = "admin",
    permissions: dict[str, list[str]] | None = None,
    role_id: uuid.UUID | None = None,
) -> Any:
    """Строит `Principal` c ролью (`role_id` — для per-node фильтра видимости, ADR-059).

    Супер-админ без явного `user_id` → константа `SUPERADMIN_USER_ID` (строка-якорь).
    """
    from app.api.deps import Principal
    from app.domain.permissions import full_catalog_permissions
    from app.domain.superadmin import SUPERADMIN_USER_ID

    if user_id is None:
        user_id = SUPERADMIN_USER_ID if is_superadmin else uuid.uuid4()

    return Principal(
        username="tester",
        role=role,
        permissions=full_catalog_permissions() if permissions is None else permissions,
        is_superadmin=is_superadmin,
        user_id=user_id,
        role_id=role_id,
    )


# --- seed-хелперы -----------------------------------------------------------


async def seed_role(
    session: AsyncSession,
    *,
    name: str | None = None,
    permissions: dict[str, list[str]] | None = None,
) -> Role:
    role = Role(
        name=name or f"role-{uuid.uuid4().hex[:8]}",
        permissions=permissions or {"documents": ["view"]},
    )
    session.add(role)
    await session.flush()
    return role


async def seed_user(
    session: AsyncSession,
    role: Role,
    *,
    username: str | None = None,
) -> User:
    user = User(
        username=username or f"user-{uuid.uuid4().hex[:10]}",
        role_id=role.id,
        password_hash="x",
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def seed_team(session: AsyncSession, *, name: str | None = None) -> Team:
    team = Team(name=name or f"team-{uuid.uuid4().hex[:8]}", leader_id=None)
    session.add(team)
    await session.flush()
    return team


async def add_membership(session: AsyncSession, user_id: uuid.UUID, team_id: uuid.UUID) -> None:
    await session.execute(insert(user_teams).values(user_id=user_id, team_id=team_id))


def superadmin_id() -> uuid.UUID:
    from app.domain.superadmin import SUPERADMIN_USER_ID

    return SUPERADMIN_USER_ID


async def seed_node(
    session: AsyncSession,
    *,
    node_type: str = "folder",
    parent_id: uuid.UUID | None = None,
    name: str | None = None,
    content_md: str | None = None,
    owner_id: uuid.UUID | None = None,
    visibility_mode: str = "inherit",
    position: int = 0,
    deleted_at: Any = None,
) -> DocumentNode:
    """Создаёт узел напрямую (минуя сервис) для сборки деревьев/сценариев видимости."""
    node = DocumentNode(
        node_type=node_type,
        parent_id=parent_id,
        name=name or (f"doc-{uuid.uuid4().hex[:6]}" if node_type == "document" else "Папка"),
        content_md=content_md,
        owner_id=owner_id or superadmin_id(),
        visibility_mode=visibility_mode,
        position=position,
        deleted_at=deleted_at,
    )
    session.add(node)
    await session.flush()
    await session.refresh(node)
    return node


async def set_node_roles(
    session: AsyncSession, node_id: uuid.UUID, role_ids: list[uuid.UUID]
) -> None:
    """Прямая запись строк `document_node_roles` (набор ролей `restricted`-узла)."""
    if role_ids:
        await session.execute(
            insert(document_node_roles),
            [{"node_id": node_id, "role_id": rid} for rid in role_ids],
        )
