"""Тест alembic-миграции 0027 `user_channel_teams` (ADR-055 §2.4) — реальный Postgres.

Проверяется САМА миграция (а не `create_all` по моделям), поэлементно по §2.1/§2.2/§2.4:

- цепочка ревизий (`0027` — голова, предок `0026`) и предел `VARCHAR(32)` для id;
- `upgrade()`: таблица `user_channel_teams` с составным PK `(user_id, channel, team_id)`,
  ОБЯЗАТЕЛЬНЫЙ индекс `ix_user_channel_teams_team_id` (без него `ON DELETE CASCADE` при
  `DELETE /api/teams/{id}` шёл бы seq scan'ом), оба FK с `ON DELETE CASCADE`, CHECK
  `channel IN ('mail','sms')`, плюс колонки `users.mail_includes_unassigned` /
  `users.sms_includes_unassigned` (`boolean NOT NULL DEFAULT false`);
- **CHECK канала** реально отвергает третий канал (`telegram` → `IntegrityError`);
- **каскады**: `DELETE` команды и `DELETE` пользователя снимают строки добавок (§2.1 —
  нормализация для этих путей не нужна именно потому, что каскадит БД);
- **backfill НЕ выполняется** (§2.4): у существующего пользователя после `upgrade` нет ни
  одной добавки, а флаги — `false` ⇒ эффективный scope канала тождественно равен
  `user_teams` (регрессии видимости нет — ровно то, что действует до миграции);
- рабочий `downgrade()`: таблица дропается, ОБЕ колонки снимаются.

Схема восстанавливается до head после каждого теста (изоляция от create_all-тестов).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

_REV_0026 = "0026_users_is_system"
_REV_0027 = "0027_user_channel_teams"  # 23 символа ≤ 32 (03-data-model.md #1 revision id)

_TABLE = "user_channel_teams"
_PK = "pk_user_channel_teams"
_IX_TEAM_ID = "ix_user_channel_teams_team_id"
_FK_USER = "fk_user_channel_teams_user_id"
_FK_TEAM = "fk_user_channel_teams_team_id"
_CK_CHANNEL = "ck_user_channel_teams_channel"
_FLAGS = ("mail_includes_unassigned", "sms_includes_unassigned")


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


async def _execute(url: str, sql: str, params: dict[str, Any] | None = None) -> None:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
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


async def _column_exists(url: str, table: str, column: str) -> bool:
    rows = await _fetch(
        url,
        "SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c",
        {"t": table, "c": column},
    )
    return bool(rows)


async def _seed_user_team(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Роль + пользователь + команда + базовое членство (`user_teams`) на голом SQL.

    Модели не используются намеренно: тест проверяет СХЕМУ миграции, а не ORM-слой.
    """
    role_id, user_id, team_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO roles (id, name, permissions) VALUES (:i, :n, '{}'::jsonb)"),
                {"i": role_id, "n": f"role-{uuid.uuid4().hex[:8]}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, role_id, is_active) "
                    "VALUES (:i, :u, 'x', :r, true)"
                ),
                {"i": user_id, "u": f"user-{uuid.uuid4().hex[:8]}", "r": role_id},
            )
            await conn.execute(
                text("INSERT INTO teams (id, name) VALUES (:i, :n)"),
                {"i": team_id, "n": f"team-{uuid.uuid4().hex[:8]}"},
            )
            await conn.execute(
                text("INSERT INTO user_teams (user_id, team_id) VALUES (:u, :t)"),
                {"u": user_id, "t": team_id},
            )
    finally:
        await engine.dispose()
    return user_id, team_id


@pytest.fixture
def alembic_cfg(monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — тест миграции 0027 требует реального "
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
    """0027 — голова цепочки, её предок — 0026; id укладывается в VARCHAR(32)."""
    script = ScriptDirectory.from_config(_alembic_config())

    assert len(script.get_heads()) == 1
    assert script.get_revision(_REV_0027).down_revision == _REV_0026
    assert len(_REV_0027) <= 32


def test_upgrade_creates_table_with_pk_index_cascade_fks_and_flags(alembic_cfg: Config) -> None:
    """Форма схемы после `upgrade 0027` (ADR-055 §2.1/§2.2): PK, индекс, CASCADE-FK, флаги."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0026)

    # До миграции ни таблицы добавок, ни флагов «Без команды» нет.
    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is False
    for flag in _FLAGS:
        assert asyncio.run(_column_exists(_DB_URL, "users", flag)) is False

    command.upgrade(alembic_cfg, _REV_0027)

    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is True

    # Составной PK (user_id, channel, team_id) — добавка не дублируется; префикс
    # (user_id, channel) обслуживает основную выборку «доп-команды канала».
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
    assert [row[0] for row in pk_columns] == ["user_id", "channel", "team_id"]

    # ОБЯЗАТЕЛЬНЫЙ индекс по team_id (§2.1): PK ведёт с user_id и поиск по team_id не
    # обслуживает — без индекса CASCADE при удалении команды шёл бы seq scan'ом.
    indexes = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT indexdef FROM pg_indexes WHERE tablename = :t AND indexname = :i",
            {"t": _TABLE, "i": _IX_TEAM_ID},
        )
    )
    assert len(indexes) == 1
    assert "team_id" in indexes[0][0]

    # Оба FK — ON DELETE CASCADE ('c'): удаление команды/пользователя снимает добавки.
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
    by_name = {row[0]: (_as_char(row[1]), row[2]) for row in fks}
    assert by_name[_FK_USER] == ("c", "users")
    assert by_name[_FK_TEAM] == ("c", "teams")

    # CHECK канала (§2.1 — `text` + CHECK вместо PG-enum).
    checks = asyncio.run(
        _fetch(
            _DB_URL,
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class src ON src.oid = c.conrelid
            WHERE src.relname = :t AND c.conname = :n AND c.contype = 'c'
            """,
            {"t": _TABLE, "n": _CK_CHANNEL},
        )
    )
    assert len(checks) == 1
    assert "mail" in checks[0][0] and "sms" in checks[0][0]

    # Колонки-флаги «Без команды» (§2.2): boolean NOT NULL DEFAULT false.
    for flag in _FLAGS:
        column = asyncio.run(
            _fetch(
                _DB_URL,
                """
                SELECT is_nullable, column_default, data_type
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = :c
                """,
                {"c": flag},
            )
        )
        assert column[0][0] == "NO"
        assert "false" in (column[0][1] or "")
        assert column[0][2] == "boolean"


def test_check_constraint_rejects_third_channel(alembic_cfg: Config) -> None:
    """CHECK `channel IN ('mail','sms')` реально отвергает третий канал (§2.1)."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0027)
    user_id, team_id = asyncio.run(_seed_user_team(_DB_URL))

    # Легальные каналы вставляются.
    for channel in ("mail", "sms"):
        asyncio.run(
            _execute(
                _DB_URL,
                "INSERT INTO user_channel_teams (user_id, channel, team_id) " "VALUES (:u, :c, :t)",
                {"u": user_id, "c": channel, "t": team_id},
            )
        )

    # Третий канал — нарушение CHECK.
    with pytest.raises(IntegrityError):
        asyncio.run(
            _execute(
                _DB_URL,
                "INSERT INTO user_channel_teams (user_id, channel, team_id) "
                "VALUES (:u, 'telegram', :t)",
                {"u": user_id, "t": team_id},
            )
        )


def test_delete_team_cascades_extras(alembic_cfg: Config) -> None:
    """`DELETE` команды снимает её добавки обоих каналов (FK ON DELETE CASCADE, §2.1)."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0027)
    user_id, team_id = asyncio.run(_seed_user_team(_DB_URL))
    for channel in ("mail", "sms"):
        asyncio.run(
            _execute(
                _DB_URL,
                "INSERT INTO user_channel_teams (user_id, channel, team_id) VALUES (:u, :c, :t)",
                {"u": user_id, "c": channel, "t": team_id},
            )
        )

    asyncio.run(_execute(_DB_URL, "DELETE FROM teams WHERE id = :t", {"t": team_id}))

    rows = asyncio.run(_fetch(_DB_URL, f"SELECT 1 FROM {_TABLE}"))
    assert rows == []


def test_delete_user_cascades_extras(alembic_cfg: Config) -> None:
    """`DELETE` пользователя снимает его добавки обоих каналов (FK ON DELETE CASCADE, §2.1)."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0027)
    user_id, team_id = asyncio.run(_seed_user_team(_DB_URL))
    for channel in ("mail", "sms"):
        asyncio.run(
            _execute(
                _DB_URL,
                "INSERT INTO user_channel_teams (user_id, channel, team_id) VALUES (:u, :c, :t)",
                {"u": user_id, "c": channel, "t": team_id},
            )
        )

    asyncio.run(_execute(_DB_URL, "DELETE FROM users WHERE id = :u", {"u": user_id}))

    rows = asyncio.run(_fetch(_DB_URL, f"SELECT 1 FROM {_TABLE}"))
    assert rows == []


def test_upgrade_does_not_backfill_existing_user_scope(alembic_cfg: Config) -> None:
    """Backfill НЕ выполняется (§2.4): scope существующего пользователя не меняется.

    Регрессионный кейс миграции: пользователь заведён ДО `0027` с базовым членством в
    команде. После `upgrade` его эффективный scope канала (`user_teams ∪ добавка`) обязан
    остаться ТЕМ ЖЕ набором `user_teams` — добавок ноль, флаги `false`.
    """
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0026)
    user_id, team_id = asyncio.run(_seed_user_team(_DB_URL))

    before = asyncio.run(
        _fetch(_DB_URL, "SELECT team_id FROM user_teams WHERE user_id = :u", {"u": user_id})
    )
    assert [row[0] for row in before] == [team_id]

    command.upgrade(alembic_cfg, _REV_0027)

    # Ни одной строки добавки не создано — переносить нечего (§2.4).
    assert asyncio.run(_fetch(_DB_URL, f"SELECT 1 FROM {_TABLE}")) == []
    # Базовое членство не тронуто ⇒ эффективный scope обоих каналов = {team_id}.
    after = asyncio.run(
        _fetch(_DB_URL, "SELECT team_id FROM user_teams WHERE user_id = :u", {"u": user_id})
    )
    assert [row[0] for row in after] == [team_id]
    # Флаги «Без команды» — false ⇒ бесхозные объекты видимости не добавляют.
    flags = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT mail_includes_unassigned, sms_includes_unassigned " "FROM users WHERE id = :u",
            {"u": user_id},
        )
    )
    assert flags[0] == (False, False)


def test_downgrade_drops_table_and_both_flag_columns(alembic_cfg: Config) -> None:
    """`downgrade()` рабочий: таблица дропается, ОБЕ колонки-флага снимаются (§2.4)."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0027)
    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is True

    command.downgrade(alembic_cfg, _REV_0026)

    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is False
    for flag in _FLAGS:
        assert asyncio.run(_column_exists(_DB_URL, "users", flag)) is False


def test_upgrade_downgrade_upgrade_round_trip(alembic_cfg: Config) -> None:
    """Round-trip `0027 → 0026 → 0027`: миграция повторно накатывается без ошибок."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0027)
    command.downgrade(alembic_cfg, _REV_0026)
    command.upgrade(alembic_cfg, _REV_0027)

    assert asyncio.run(_table_exists(_DB_URL, _TABLE)) is True
    for flag in _FLAGS:
        assert asyncio.run(_column_exists(_DB_URL, "users", flag)) is True
