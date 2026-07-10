"""Инфраструктура integration-тестов модуля «Почты» S1 (реальный Postgres, ADR-044).

Вспомогательный модуль (без `test_`-префикса — pytest не коллектит). Даёт `mail_db()`
(async-engine + sessionmaker поверх реального Postgres; `create_all` + TRUNCATE mail-
таблиц для изоляции) и seed-хелперы (роль/пользователь/команда/ящик).

**Важно:** модуль НЕ импортирует `app.main`/`app.api.deps` (они на момент S1 тянут старый
proxy `mail_service.py`, ломающийся о снятый `MailOrder` в ходе параллельного S3-рефактора).
Тесты работают с репозиториями/сервисами приёма напрямую поверх тест-сессии — этого
достаточно для покрытия движка тегов, идемпотентности приёма, keyset-ленты, статус-канала.

Реальный URL БД захватывается на импорте (до autouse-фикстуры conftest, monkeypatch'ащей
DATABASE_URL на фейковый). CI задаёт DATABASE_URL (postgres:16); локально — TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from app.models import Base
from app.models.mail_account import MailAccount
from app.models.role import Role
from app.models.team import Team, user_teams
from app.models.user import User
from sqlalchemy import insert
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

# Очищаемые mail-таблицы + смежные (teams/users/roles — FK ящиков/линков/settings).
# CASCADE снимает зависимые mail_messages/mail_message_tags и т.п.
_TRUNCATE = (
    "mail_message_tags, mail_tag_rules, mail_tags, mail_telegram_notifications, "
    "mail_telegram_links, mail_user_settings, mail_messages, mail_accounts, "
    "user_teams, teams, users, roles"
)


@asynccontextmanager
async def mail_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessionmaker поверх реального Postgres; чистая схема + TRUNCATE для изоляции."""
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — integration-тесты почты требуют "
            "реального Postgres (CI поднимает postgres:16; локально — контейнер)."
        )
    engine = create_async_engine(_DB_URL, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(sa_text(f"TRUNCATE {_TRUNCATE} RESTART IDENTITY CASCADE"))
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


async def seed_role(
    session: AsyncSession, *, permissions: dict[str, list[str]] | None = None
) -> Role:
    role = Role(name=f"role-{uuid.uuid4().hex[:8]}", permissions=permissions or {"mail": ["view"]})
    session.add(role)
    await session.flush()
    return role


async def seed_user(
    session: AsyncSession,
    role: Role,
    *,
    username: str | None = None,
    telegram: str | None = None,
) -> User:
    user = User(
        username=username or f"user-{uuid.uuid4().hex[:10]}",
        role_id=role.id,
        password_hash="x",
        is_active=True,
        telegram=telegram,
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


async def seed_account(
    session: AsyncSession,
    *,
    account_id: int,
    email: str | None = None,
    team_id: uuid.UUID | None = None,
    is_active: bool = True,
    down_alert_sent_at: datetime | None = None,
) -> MailAccount:
    """Создаёт ящик каталога `mail_accounts` (id = id в агрегаторе, не autoincrement)."""
    account = MailAccount(
        id=account_id,
        email=email or f"box{account_id}@example.com",
        team_id=team_id,
        is_active=is_active,
        down_alert_sent_at=down_alert_sent_at,
    )
    session.add(account)
    await session.flush()
    return account


def dt(year: int = 2026, month: int = 1, day: int = 1, hour: int = 0, minute: int = 0) -> datetime:
    """Aware UTC datetime — детерминированный `internal_date` для keyset-тестов."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)
