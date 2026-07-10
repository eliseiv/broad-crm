"""Тест alembic-миграции 0021 (create mail module, ADR-044 §2) — реальный Postgres.

Проверяет: (1) единственный head (нет ветвления цепочки миграций); (2) upgrade
0020→0021 создаёт все 8 mail-таблиц; (3) `teams.mail_group_id` НЕ трогается (его drop —
S3, после миграции данных); (4) downgrade 0021→0020 удаляет mail-таблицы, `mail_group_id`
остаётся. Схема восстанавливается до head после каждого теста (изоляция от create_all-тестов).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

_REV_0020 = "0020_backends_domain_canon"
_REV_0021 = "0021_create_mail_module"

_MAIL_TABLES = (
    "mail_accounts",
    "mail_messages",
    "mail_tags",
    "mail_tag_rules",
    "mail_message_tags",
    "mail_telegram_links",
    "mail_telegram_notifications",
    "mail_user_settings",
)


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


async def _drop_schema(url: str) -> None:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _table_exists(url: str, table: str) -> bool:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"})
            return result.scalar() is not None
    finally:
        await engine.dispose()


async def _column_exists(url: str, table: str, column: str) -> bool:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            )
            return result.first() is not None
    finally:
        await engine.dispose()


@pytest.fixture
def alembic_cfg(monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — тест миграции 0021 требует реального "
            "Postgres (CI поднимает postgres:16; локально — контейнер)."
        )
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    from app.config import get_settings

    get_settings.cache_clear()
    yield _alembic_config()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _restore_head_after_test(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Дропает public-схему и накатывает head после теста (чистый старт для create_all)."""
    yield
    if not _DB_URL:
        return
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    from app.config import get_settings

    get_settings.cache_clear()
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(_alembic_config(), "head")
    get_settings.cache_clear()


# --------------------------------------------------------------- единственный head
def test_single_migration_head() -> None:
    """Цепочка миграций линейна — ровно один head (нет ветвления)."""
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    assert len(heads) == 1, f"ожидался один head, получено: {heads}"
    # 0021 — корректный узел цепочки (ревизует 0020), даже если поверх лёг S3-0022.
    assert script.get_revision(_REV_0021).down_revision == _REV_0020


# ----------------------------------------------------------- upgrade 0020→0021
def test_upgrade_creates_mail_tables_and_keeps_mail_group_id(alembic_cfg: Config) -> None:
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0020)

    # До 0021 mail-таблиц ещё нет, а teams.mail_group_id уже есть (миграция 0018).
    assert asyncio.run(_table_exists(_DB_URL, "mail_accounts")) is False
    assert asyncio.run(_column_exists(_DB_URL, "teams", "mail_group_id")) is True

    command.upgrade(alembic_cfg, _REV_0021)

    for table in _MAIL_TABLES:
        assert asyncio.run(_table_exists(_DB_URL, table)) is True, f"{table} не создана"
    # teams.mail_group_id НЕ трогается миграцией 0021 (его drop — S3).
    assert asyncio.run(_column_exists(_DB_URL, "teams", "mail_group_id")) is True


# --------------------------------------------------------- downgrade 0021→0020
def test_downgrade_drops_mail_tables_keeps_mail_group_id(alembic_cfg: Config) -> None:
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0021)
    command.downgrade(alembic_cfg, _REV_0020)

    for table in _MAIL_TABLES:
        assert asyncio.run(_table_exists(_DB_URL, table)) is False, f"{table} не удалена"
    assert asyncio.run(_column_exists(_DB_URL, "teams", "mail_group_id")) is True
