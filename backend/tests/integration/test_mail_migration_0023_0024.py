"""Тесты alembic-миграций 0023 и 0024 (ADR-047 §1/§3) — реальный Postgres.

**0023 `mail_tags_drop_is_builtin`:** порядок шагов (сначала идемпотентный data-seed 10
канонических тегов + их правил, потом `DROP COLUMN is_builtin`); сев не дублирует уже
существующие теги (`ON CONFLICT (name) DO NOTHING`) и НЕ дублирует им правила при повторном
прогоне; `downgrade` возвращает только ФОРМУ схемы (колонку с default false), значения
признака не восстанавливаются.

**0024 `mail_accounts_num_app_name`:** добавление `number`/`app_name` + backfill из
`display_name` нормативным правилом ADR-047 §3.1 (кейсы владельца воспроизводятся
побуквенно); `display_name` при backfill НЕ трогается; `downgrade` дропает обе колонки,
`display_name` остаётся. Плюс §3.5: длина `revision` ≤ 32 символов (жёсткий предел
`alembic_version.version_num VARCHAR(32)`).

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

_REV_0022 = "0022_create_mail_sent_messages"
_REV_0023 = "0023_mail_tags_drop_is_builtin"
_REV_0024 = "0024_mail_accounts_num_app_name"  # укорочен до 31 симв. (ADR-047 §3.5)

# Канонический каталог сева 0023 (ADR-047 §1) — набор имён, вшитый в тело миграции.
_CANONICAL_NAMES = {
    "DPLA.PLA",
    "Отменить подписку",
    "Продление аккаунта",
    "Диспут",
    "Бан Аккаунта",
    "Релиз",
    "Реджект",
    "Ревью",
    "Ждет Ревью",
    "Нужна замена реквизитов",
}


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


async def _exec(url: str, sql: str, params: dict[str, Any] | None = None) -> None:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


async def _column_exists(url: str, table: str, column: str) -> bool:
    rows = await _fetch(
        url,
        "SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c",
        {"t": table, "c": column},
    )
    return bool(rows)


@pytest.fixture
def alembic_cfg(monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — тесты миграций 0023/0024 требуют "
            "реального Postgres (CI поднимает postgres:16; локально — контейнер)."
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


# ------------------------------------------------ §3.5: длина revision id ≤ 32
def test_all_revision_ids_fit_alembic_version_num_varchar32() -> None:
    """`alembic_version.version_num` — VARCHAR(32): id длиннее физически неприменим."""
    script = ScriptDirectory.from_config(_alembic_config())
    too_long = [rev.revision for rev in script.walk_revisions() if len(rev.revision) > 32]
    assert too_long == [], f"revision id длиннее 32 символов: {too_long}"


def test_single_head_and_chain_0022_0023_0024() -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    assert len(script.get_heads()) == 1
    assert script.get_revision(_REV_0023).down_revision == _REV_0022
    assert script.get_revision(_REV_0024).down_revision == _REV_0023


# ------------------------------------------------------- 0023: сев + drop колонки
def test_0023_seeds_canonical_tags_with_rules_and_drops_is_builtin(alembic_cfg: Config) -> None:
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0022)

    # До 0023: колонка is_builtin есть, тегов нет.
    assert asyncio.run(_column_exists(_DB_URL, "mail_tags", "is_builtin")) is True
    assert asyncio.run(_fetch(_DB_URL, "SELECT count(*) FROM mail_tags"))[0][0] == 0

    command.upgrade(alembic_cfg, _REV_0023)

    names = {row[0] for row in asyncio.run(_fetch(_DB_URL, "SELECT name FROM mail_tags"))}
    assert names == _CANONICAL_NAMES
    # Правила засеяны вместе с тегами (у каждого канонического тега — хотя бы одно).
    rules = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT t.name, count(r.id) FROM mail_tags t "
            "LEFT JOIN mail_tag_rules r ON r.tag_id = t.id GROUP BY t.name",
        )
    )
    assert all(count > 0 for _, count in rules), rules
    # Колонка признака дропнута (шаг 2 — ПОСЛЕ сева).
    assert asyncio.run(_column_exists(_DB_URL, "mail_tags", "is_builtin")) is False


def test_0023_seed_is_idempotent_existing_tag_kept_rules_not_duplicated(
    alembic_cfg: Config,
) -> None:
    """Тег с занятым именем не трогается (ON CONFLICT DO NOTHING) и правила ему не дублируются."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0022)

    # Прод-подобное состояние: тег «Диспут» уже существует (со своим цветом и правилом).
    asyncio.run(
        _exec(
            _DB_URL,
            "INSERT INTO mail_tags (name, color, match_mode, is_builtin) "
            "VALUES ('Диспут', '#000000', 'any', true)",
        )
    )
    asyncio.run(
        _exec(
            _DB_URL,
            "INSERT INTO mail_tag_rules (tag_id, type, pattern) "
            "SELECT id, 'subject_contains', 'моё-правило' FROM mail_tags WHERE name = 'Диспут'",
        )
    )

    command.upgrade(alembic_cfg, _REV_0023)

    # Дубля имени нет, цвет существующего тега сохранён (сев его не перезаписывает).
    rows = asyncio.run(_fetch(_DB_URL, "SELECT color FROM mail_tags WHERE name = 'Диспут'"))
    assert len(rows) == 1
    assert rows[0][0] == "#000000"
    # Правила существующему тегу НЕ добавлялись (вставляются только впервые созданным).
    dispute_rules = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT r.pattern FROM mail_tag_rules r JOIN mail_tags t ON t.id = r.tag_id "
            "WHERE t.name = 'Диспут'",
        )
    )
    assert [p for (p,) in dispute_rules] == ["моё-правило"]
    # Остальные 9 канонических тегов созданы.
    names = {row[0] for row in asyncio.run(_fetch(_DB_URL, "SELECT name FROM mail_tags"))}
    assert names == _CANONICAL_NAMES


def test_0023_downgrade_restores_column_shape_only(alembic_cfg: Config) -> None:
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0023)
    command.downgrade(alembic_cfg, _REV_0022)

    assert asyncio.run(_column_exists(_DB_URL, "mail_tags", "is_builtin")) is True
    # Значения признака НЕ восстанавливаются — он упразднён: у всех строк default false.
    flags = asyncio.run(_fetch(_DB_URL, "SELECT DISTINCT is_builtin FROM mail_tags"))
    assert [f for (f,) in flags] == [False]
    # Засеянные теги остаются (downgrade сев не откатывает).
    assert asyncio.run(_fetch(_DB_URL, "SELECT count(*) FROM mail_tags"))[0][0] == len(
        _CANONICAL_NAMES
    )


def test_0023_reapply_after_downgrade_does_not_duplicate_tags_or_rules(
    alembic_cfg: Config,
) -> None:
    """Повторный прогон сева (downgrade → upgrade) не плодит ни тегов, ни правил."""
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0023)
    rules_before = asyncio.run(_fetch(_DB_URL, "SELECT count(*) FROM mail_tag_rules"))[0][0]

    command.downgrade(alembic_cfg, _REV_0022)
    command.upgrade(alembic_cfg, _REV_0023)

    assert asyncio.run(_fetch(_DB_URL, "SELECT count(*) FROM mail_tags"))[0][0] == len(
        _CANONICAL_NAMES
    )
    assert asyncio.run(_fetch(_DB_URL, "SELECT count(*) FROM mail_tag_rules"))[0][0] == rules_before


# ------------------------------------------------- 0024: колонки + backfill правилом §3.1
_BACKFILL_ROWS = [
    # (id, display_name, ожидаемый number, ожидаемый app_name) — кейсы владельца §3.1.
    (901, "5108 Klyro Forge (Codex)", "5108", "Klyro Forge (Codex)"),
    (902, "173, 57, 104", "173, 57, 104", None),
    (903, "WIU", None, "WIU"),
    (904, None, None, None),  # display_name IS NULL → обе колонки NULL
    (905, "173,57 ,  104", "173, 57, 104", None),  # нормализация разделителя
]


def test_0024_adds_columns_and_backfills_by_normative_rule(alembic_cfg: Config) -> None:
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0023)

    assert asyncio.run(_column_exists(_DB_URL, "mail_accounts", "number")) is False
    assert asyncio.run(_column_exists(_DB_URL, "mail_accounts", "app_name")) is False

    for account_id, display_name, _, _ in _BACKFILL_ROWS:
        asyncio.run(
            _exec(
                _DB_URL,
                "INSERT INTO mail_accounts (id, email, display_name) " "VALUES (:id, :email, :dn)",
                {"id": account_id, "email": f"box{account_id}@example.com", "dn": display_name},
            )
        )

    command.upgrade(alembic_cfg, _REV_0024)

    assert asyncio.run(_column_exists(_DB_URL, "mail_accounts", "number")) is True
    assert asyncio.run(_column_exists(_DB_URL, "mail_accounts", "app_name")) is True

    rows = asyncio.run(
        _fetch(
            _DB_URL,
            "SELECT id, number, app_name, display_name FROM mail_accounts ORDER BY id",
        )
    )
    actual = {row[0]: (row[1], row[2], row[3]) for row in rows}
    for account_id, display_name, number, app_name in _BACKFILL_ROWS:
        assert actual[account_id][:2] == (number, app_name), f"backfill id={account_id}"
        # `display_name` backfill'ом НЕ трогается (остаётся как было; пересчёт — при
        # следующей правке ящика сервером).
        assert actual[account_id][2] == display_name


def test_0024_downgrade_drops_columns_and_keeps_display_name(alembic_cfg: Config) -> None:
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(alembic_cfg, _REV_0023)
    asyncio.run(
        _exec(
            _DB_URL,
            "INSERT INTO mail_accounts (id, email, display_name) "
            "VALUES (910, 'box910@example.com', '5108 Klyro')",
        )
    )
    command.upgrade(alembic_cfg, _REV_0024)
    command.downgrade(alembic_cfg, _REV_0023)

    assert asyncio.run(_column_exists(_DB_URL, "mail_accounts", "number")) is False
    assert asyncio.run(_column_exists(_DB_URL, "mail_accounts", "app_name")) is False
    # Данные не теряются: display_name на месте (в нём склейка).
    rows = asyncio.run(_fetch(_DB_URL, "SELECT display_name FROM mail_accounts WHERE id = 910"))
    assert rows[0][0] == "5108 Klyro"
