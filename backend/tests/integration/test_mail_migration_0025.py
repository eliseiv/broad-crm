"""Тест alembic-миграции 0025 `mail_message_reads` (ADR-050 §2.1) — реальный Postgres.

Проверяется САМА миграция (а не `create_all` по моделям): цепочка ревизий, составной PK
`pk_mail_message_reads (user_id, message_id)`, ОБЯЗАТЕЛЬНЫЙ индекс
`ix_mail_message_reads_message_id` (без него `ON DELETE CASCADE` со стороны `mail_messages`
шёл бы seq scan'ом), оба FK с `ON DELETE CASCADE`, `read_at NOT NULL DEFAULT now()` и рабочий
`downgrade()` (миграция чисто аддитивная — backfill не нужен: пусто = «всё непрочитано»).

Схема восстанавливается до head после каждого теста (изоляция от create_all-тестов).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

_REV_0024 = "0024_mail_accounts_num_app_name"
_REV_0025 = "0025_mail_message_reads"  # 23 символа ≤ 32 (ADR-047 §3.5)

_TABLE = "mail_message_reads"
_PK = "pk_mail_message_reads"
_IX_MESSAGE_ID = "ix_mail_message_reads_message_id"
_FK_USER = "fk_mail_message_reads_user_id"
_FK_MESSAGE = "fk_mail_message_reads_message_id"


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


async def _fetch(url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return list(result.all())
    finally:
        await engine.dispose()


def _as_char(value: Any) -> str:
    """Значение pg-типа `"char"` (asyncpg отдаёт bytes, psycopg — str) → строка."""
    return value.decode() if isinstance(value, bytes) else str(value)


async def _table_exists(url: str, table: str) -> bool:
    rows = await _fetch(
        url,
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t",
        {"t": table},
    )
    return bool(rows)


@pytest.fixture
def alembic_cfg(monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — тест миграции 0025 требует реального "
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


def test_revision_chain_and_single_head() -> None:
    """0025 — голова цепочки, её предок — 0024; id укладывается в VARCHAR(32)."""
    script = ScriptDirectory.from_config(_alembic_config())

    assert len(script.get_heads()) == 1
    assert script.get_revision(_REV_0025).down_revision == _REV_0024
    assert len(_REV_0025) <= 32


def test_upgrade_creates_table_with_pk_index_and_cascade_fks(alembic_cfg: Config) -> None:
    """Форма схемы после `upgrade 0025`: PK, обязательный индекс, оба FK с CASCADE."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0024)

    # До миграции таблицы нет.
    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is False

    command.upgrade(alembic_cfg, _REV_0025)

    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is True

    # Составной PK (user_id, message_id) — он же рабочий индекс обоих горячих путей ленты.
    pk_columns = asyncio.run(
        _fetch(
            _DB_URL,
            """
            SELECT a.attname
            FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
            WHERE c.conname = :name AND c.contype = 'p'
            ORDER BY array_position(c.conkey, a.attnum)
            """,
            {"name": _PK},
        )
    )
    assert [row[0] for row in pk_columns] == ["user_id", "message_id"]

    # ОБЯЗАТЕЛЬНЫЙ индекс по message_id (ADR-050 §2.1): PK ведёт с user_id и поиск по
    # message_id не обслуживает — без индекса CASCADE-удаление писем шло бы seq scan'ом.
    indexes = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT indexdef FROM pg_indexes WHERE tablename = :t AND indexname = :i",
            {"t": _TABLE, "i": _IX_MESSAGE_ID},
        )
    )
    assert len(indexes) == 1
    assert "message_id" in indexes[0][0]

    # Оба FK — ON DELETE CASCADE ('c'): удаление письма/ящика/пользователя чистит отметки.
    fks = asyncio.run(
        _fetch(
            _DB_URL,
            """
            SELECT c.conname, c.confdeltype, ref.relname
            FROM pg_constraint c
            JOIN pg_class src ON src.oid = c.conrelid
            JOIN pg_class ref ON ref.oid = c.confrelid
            WHERE src.relname = :t AND c.contype = 'f'
            """,
            {"t": _TABLE},
        )
    )
    # `confdeltype` — тип `"char"`; asyncpg отдаёт его как bytes → нормализуем к строке.
    by_name = {row[0]: (_as_char(row[1]), row[2]) for row in fks}
    assert by_name[_FK_USER] == ("c", "users")
    assert by_name[_FK_MESSAGE] == ("c", "mail_messages")

    # read_at — NOT NULL с server_default now() (диагностика; наружу не отдаётся).
    read_at = asyncio.run(
        _fetch(
            _DB_URL,
            """
            SELECT is_nullable, column_default, data_type
            FROM information_schema.columns
            WHERE table_name = :t AND column_name = 'read_at'
            """,
            {"t": _TABLE},
        )
    )
    assert read_at[0][0] == "NO"
    assert "now()" in (read_at[0][1] or "")
    assert read_at[0][2] == "timestamp with time zone"


def test_downgrade_drops_table(alembic_cfg: Config) -> None:
    """`downgrade()` рабочий: таблица дропается, цепочка возвращается на 0024."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0025)
    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is True

    command.downgrade(alembic_cfg, _REV_0024)

    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is False
    # Смежные таблицы на месте — миграция аддитивная, ничего чужого не трогает.
    assert asyncio.run(_table_exists(_DB_URL, "mail_messages")) is True
    assert asyncio.run(_table_exists(_DB_URL, "users")) is True
