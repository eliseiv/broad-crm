"""Регресс-гейт `downgrade()` миграции 0038 на реальном Postgres (ADR-079 §1).

Проверяется ровно то, что было найдено ревью: пользователь **без единой роли** (заведён
прямым SQL в обход API — «минимум одна роль» держит сервис, а не БД) не даёт восстановить
`NOT NULL users.role_id`, и откат обязан падать ВНЯТНО, называя такие строки, а не
опаковым нарушением констрейнта посреди миграции. Подставлять такому пользователю
произвольную роль запрещено — это молча выдало бы ему чужие права.

Заодно фиксируется happy-path: у пользователя с двумя ролями откат восстанавливает
ПЕРВУЮ (`MIN(created_at)`), а не произвольную.

Полное покрытие миграций 0038/0039/0040 (перенос якоря, backfill ФИО, DDL снимка) — зона qa.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from mail_s34_helpers import mail_db
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Модуль миграции грузится по ПУТИ: `alembic/versions` — не пакет, а имя файла
# начинается с цифры, поэтому обычный импорт невозможен.
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0038_user_roles_m2m.py"
)
_SPEC = importlib.util.spec_from_file_location("migration_0038", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)


async def _run(sm: async_sessionmaker[AsyncSession], step: str) -> None:
    """Прогоняет `upgrade()`/`downgrade()` 0038 поверх актуальной схемы."""
    async with sm() as session:
        conn = await session.connection()

        def _apply(sync_conn: object) -> None:
            context = MigrationContext.configure(sync_conn)  # type: ignore[arg-type]
            with Operations.context(context):
                getattr(_MIGRATION, step)()

        await conn.run_sync(_apply)
        await session.commit()


@asynccontextmanager
async def _reversible_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """БД, чья схема ГАРАНТИРОВАННО возвращается к head после теста.

    Откат физически меняет схему (`DROP TABLE user_roles`, `ADD COLUMN users.role_id
    NOT NULL`), а тестовая БД одна на весь прогон: без обратного `upgrade()` соседние
    файлы получили бы схему предыдущей ревизии и падали бы на вставке пользователей.
    `metadata.create_all` эту порчу НЕ лечит — лишнюю колонку он не снимает.
    """
    async with mail_db() as sm:
        try:
            yield sm
        finally:
            has_column = await _has_users_role_id(sm)
            if has_column:
                await _run(sm, "upgrade")


async def _has_users_role_id(sm: async_sessionmaker[AsyncSession]) -> bool:
    async with sm() as session:
        found = await session.execute(
            sa_text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'users' AND column_name = 'role_id'"
            )
        )
        return found.first() is not None


async def _seed_role(session: AsyncSession, name: str) -> uuid.UUID:
    role_id = uuid.uuid4()
    await session.execute(
        sa_text("INSERT INTO roles (id, name, permissions) VALUES (:id, :n, '{}'::jsonb)"),
        {"id": str(role_id), "n": name},
    )
    return role_id


async def _seed_user(session: AsyncSession, username: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await session.execute(
        sa_text(
            "INSERT INTO users (id, username, password_hash, is_active, is_system) "
            "VALUES (:id, :u, 'x', true, false)"
        ),
        {"id": str(user_id), "u": username},
    )
    return user_id


async def _link(
    session: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID, created_at: datetime
) -> None:
    await session.execute(
        sa_text("INSERT INTO user_roles (user_id, role_id, created_at) VALUES (:u, :r, :c)"),
        {"u": str(user_id), "r": str(role_id), "c": created_at},
    )


async def test_downgrade_0038_fails_loudly_for_user_without_roles() -> None:
    """Гейт: пользователь без ролей → RuntimeError с его `username`, а не NOT NULL-ошибка."""
    async with _reversible_db() as sm:
        async with sm() as session:
            role_id = await _seed_role(session, "Оператор")
            ok_user = await _seed_user(session, "с-ролью")
            await _link(session, ok_user, role_id, datetime(2026, 1, 1, tzinfo=UTC))
            await _seed_user(session, "без-роли")
            await session.commit()

        with pytest.raises(RuntimeError) as exc:
            await _run(sm, "downgrade")

    assert "без-роли" in str(exc.value)


async def test_downgrade_0038_restores_first_role_by_created_at() -> None:
    """Happy-path: восстанавливается ПЕРВАЯ роль (`MIN(created_at)`), откат lossy осознанно."""
    async with _reversible_db() as sm:
        async with sm() as session:
            first = await _seed_role(session, "Первая")
            second = await _seed_role(session, "Вторая")
            user_id = await _seed_user(session, "двуролевой")
            await _link(session, user_id, second, datetime(2026, 5, 5, tzinfo=UTC))
            await _link(session, user_id, first, datetime(2026, 1, 1, tzinfo=UTC))
            # Якорь супер-админа уже засеян фикстурой — его строка тоже должна пережить откат.
            await session.commit()

        await _run(sm, "downgrade")

        async with sm() as session:
            restored = (
                await session.execute(
                    sa_text("SELECT role_id FROM users WHERE username = :u"),
                    {"u": "двуролевой"},
                )
            ).scalar_one()
            tables = (
                await session.execute(sa_text("SELECT to_regclass('public.user_roles')"))
            ).scalar_one()

    assert uuid.UUID(str(restored)) == first
    assert tables is None  # таблица M2M снята откатом
