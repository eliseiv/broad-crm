"""Тест alembic-миграции `0031_servers_ssh_key_auth` (ADR-067 §2) — реальный Postgres.

Проверяется САМА миграция (а не `create_all` по моделям):

- цепочка ревизий и предел `VARCHAR(32)` для id;
- **`upgrade()` без backfill** — и это следствие ПОРЯДКА шагов, а не совпадение:
  `server_default='password'` проставляется существующим строкам в момент `ADD COLUMN`, и к
  моменту создания CHECK каждая старая строка уже ему удовлетворяет. Регресс-кейс:
  «доADR-067»-сервер после `upgrade` остаётся валидным, читаемым и парольным;
- `ssh_password_encrypted` становится **nullable** (иначе key-сервер непредставим);
- оба CHECK реально работают ПОСЛЕ миграции (а не просто числятся в схеме);
- **`downgrade()` рабочий и ЛОССИ ПО СТРОКАМ**: key-серверы удаляются (в старой схеме они
  непредставимы), парольные — остаются, `ssh_password_encrypted` снова `NOT NULL`, три
  колонки сняты. Подстановка пароля-заглушки ЗАПРЕЩЕНА — тест это фиксирует, проверяя,
  что key-строка именно исчезла, а не «уцелела с пустым паролем».

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

_REV_0030 = "0030_document_node_roles"
_REV_0031 = "0031_servers_ssh_key_auth"  # 25 символов ≤ 32 (03-data-model.md #1 revision id)

_NEW_COLUMNS = ("auth_method", "ssh_private_key_encrypted", "ssh_key_passphrase_encrypted")


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


async def _column(url: str, column: str) -> tuple[str, str] | None:
    """(`data_type`, `is_nullable`) колонки `servers` или `None`, если её нет."""
    rows = await _fetch(
        url,
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'servers' AND column_name = :c",
        {"c": column},
    )
    return (rows[0][0], rows[0][1]) if rows else None


async def _seed_legacy_password_server(url: str) -> uuid.UUID:
    """Строка «доADR-067»-сервера — БЕЗ новых колонок (их ещё не существует).

    Модели не используются намеренно: тест проверяет СХЕМУ миграции, а не ORM-слой.
    """
    server_id = uuid.uuid4()
    await _execute(
        url,
        "INSERT INTO servers (id, name, ip, ssh_user, ssh_password_encrypted, exporter_port, "
        "provision_status, position) "
        "VALUES (:i, :n, :ip, 'root', :pwd, 9100, 'online', 0)",
        {
            "i": server_id,
            "n": "Legacy server",
            "ip": f"10.1.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}",
            "pwd": b"legacy-ciphertext",
        },
    )
    return server_id


async def _seed_key_server(url: str) -> uuid.UUID:
    """key-сервер (возможен только ПОСЛЕ upgrade): пароля нет, есть ключ."""
    server_id = uuid.uuid4()
    await _execute(
        url,
        "INSERT INTO servers (id, name, ip, ssh_user, auth_method, ssh_private_key_encrypted, "
        "exporter_port, provision_status, position) "
        "VALUES (:i, :n, :ip, 'root', 'key', :key, 9100, 'pending', 0)",
        {
            "i": server_id,
            "n": "Key server",
            "ip": f"10.2.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}",
            "key": b"key-ciphertext",
        },
    )
    return server_id


@pytest.fixture
def alembic_cfg(monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — тест миграции 0031 требует реального "
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


def test_revision_chain_and_id_length() -> None:
    """Предок 0031 — 0030; id укладывается в `VARCHAR(32)` таблицы `alembic_version`."""
    script = ScriptDirectory.from_config(_alembic_config())

    assert script.get_revision(_REV_0031).down_revision == _REV_0030
    assert len(_REV_0031) <= 32


def test_upgrade_adds_columns_and_relaxes_password_not_null(alembic_cfg: Config) -> None:
    """После `upgrade 0031`: три новых колонки, `ssh_password_encrypted` — nullable."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0030)

    for column in _NEW_COLUMNS:
        assert asyncio.run(_column(_DB_URL, column)) is None
    # До миграции пароль обязателен — именно это делало key-сервер непредставимым.
    assert asyncio.run(_column(_DB_URL, "ssh_password_encrypted")) == ("bytea", "NO")

    command.upgrade(alembic_cfg, _REV_0031)

    assert asyncio.run(_column(_DB_URL, "auth_method")) == ("text", "NO")
    assert asyncio.run(_column(_DB_URL, "ssh_private_key_encrypted")) == ("bytea", "YES")
    assert asyncio.run(_column(_DB_URL, "ssh_key_passphrase_encrypted")) == ("bytea", "YES")
    assert asyncio.run(_column(_DB_URL, "ssh_password_encrypted")) == ("bytea", "YES")


def test_upgrade_needs_no_backfill_existing_rows_become_password_servers(
    alembic_cfg: Config,
) -> None:
    """Существующий сервер после `upgrade` — валидный парольный (**backfill не нужен**).

    Регресс-кейс совместимости: `server_default` в `ADD COLUMN` уже проставил `'password'`
    всем строкам, поэтому CHECK, создаваемый последним шагом, для них выполняется.
    Порядок шагов миграции здесь и проверяется — при другом порядке `upgrade` упал бы.
    """
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0030)
    server_id = asyncio.run(_seed_legacy_password_server(_DB_URL))

    command.upgrade(alembic_cfg, _REV_0031)

    rows = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT auth_method, ssh_password_encrypted, ssh_private_key_encrypted, "
            "ssh_key_passphrase_encrypted FROM servers WHERE id = :i",
            {"i": server_id},
        )
    )
    assert len(rows) == 1
    auth_method, password, key, passphrase = rows[0]
    assert auth_method == "password"
    assert bytes(password) == b"legacy-ciphertext"
    assert key is None
    assert passphrase is None


def test_upgraded_schema_check_rejects_both_materials(alembic_cfg: Config) -> None:
    """CHECK работает ПОСЛЕ миграции: «пароль + ключ» отвергается БД."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0031)
    server_id = asyncio.run(_seed_legacy_password_server(_DB_URL))

    with pytest.raises(IntegrityError) as exc:
        asyncio.run(
            _execute(
                _DB_URL,
                "UPDATE servers SET ssh_private_key_encrypted = :k WHERE id = :i",
                {"k": b"key-ciphertext", "i": server_id},
            )
        )
    assert "ck_servers_auth_material" in str(exc.value)


def test_upgraded_schema_check_rejects_key_without_material(alembic_cfg: Config) -> None:
    """«key без ключа» тоже отвергается — вторая ветка CHECK."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0031)
    server_id = asyncio.run(_seed_legacy_password_server(_DB_URL))

    with pytest.raises(IntegrityError) as exc:
        asyncio.run(
            _execute(
                _DB_URL,
                "UPDATE servers SET auth_method = 'key', ssh_password_encrypted = NULL "
                "WHERE id = :i",
                {"i": server_id},
            )
        )
    assert "ck_servers_auth_material" in str(exc.value)


def test_upgraded_schema_allows_key_server_row(alembic_cfg: Config) -> None:
    """Позитивная сторона: key-строка (без пароля) в новой схеме вставляется."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0031)

    server_id = asyncio.run(_seed_key_server(_DB_URL))

    rows = asyncio.run(
        _fetch(_DB_URL, "SELECT auth_method FROM servers WHERE id = :i", {"i": server_id})
    )
    assert rows[0][0] == "key"


def test_downgrade_drops_columns_and_restores_not_null(alembic_cfg: Config) -> None:
    """`downgrade` снимает три колонки и возвращает `ssh_password_encrypted NOT NULL`."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0031)

    command.downgrade(alembic_cfg, _REV_0030)

    for column in _NEW_COLUMNS:
        assert asyncio.run(_column(_DB_URL, column)) is None
    assert asyncio.run(_column(_DB_URL, "ssh_password_encrypted")) == ("bytea", "NO")


def test_downgrade_deletes_key_servers_and_keeps_password_servers(alembic_cfg: Config) -> None:
    """**Лосси по строкам:** key-серверы удаляются, парольные остаются нетронутыми.

    Ключевой кейс: подстановка пароля-заглушки ЗАПРЕЩЕНА (после отката провижининг пошёл
    бы на хост с заведомо неверным паролем, а оператор видел бы «пароль задан»), поэтому
    проверяется именно **исчезновение** key-строки, а не её «выживание с пустым паролем».
    """
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0031)
    password_id = asyncio.run(_seed_legacy_password_server(_DB_URL))
    key_id = asyncio.run(_seed_key_server(_DB_URL))

    command.downgrade(alembic_cfg, _REV_0030)

    remaining = asyncio.run(_fetch(_DB_URL, "SELECT id, ssh_password_encrypted FROM servers"))
    ids = {row[0] for row in remaining}
    assert password_id in ids
    assert key_id not in ids
    # Уцелевшая строка сохранила ИСХОДНЫЙ пароль (downgrade его не переписывал).
    assert bytes(remaining[0][1]) == b"legacy-ciphertext"


def test_downgrade_drops_both_check_constraints(alembic_cfg: Config) -> None:
    """Оба CHECK сняты — иначе повторный `upgrade` упал бы на их создании."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0031)

    command.downgrade(alembic_cfg, _REV_0030)

    rows = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT conname FROM pg_constraint WHERE conrelid = 'servers'::regclass "
            "AND contype = 'c'",
        )
    )
    names = {row[0] for row in rows}
    assert "ck_servers_auth_method" not in names
    assert "ck_servers_auth_material" not in names


def test_upgrade_downgrade_upgrade_round_trip(alembic_cfg: Config) -> None:
    """Повторный `upgrade` после отката проходит (констрейнты/колонки не остались)."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0031)
    command.downgrade(alembic_cfg, _REV_0030)

    command.upgrade(alembic_cfg, _REV_0031)

    assert asyncio.run(_column(_DB_URL, "auth_method")) == ("text", "NO")
