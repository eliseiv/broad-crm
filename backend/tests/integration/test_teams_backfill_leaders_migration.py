"""Интеграционный тест миграции 0016 (ретроактивный backfill лидеров, ADR-026).

One-time data-fix `0016_backfill_team_leaders` прогоняется на РЕАЛЬНОМ Postgres (offline
SQL-рендер тут бесполезен: суть миграции — коррелированный `UPDATE` с подзапросом
`ORDER BY ... LIMIT 1`, который нужно проверить на фактических строках). Backend
реализовал миграцию, но не смог прогнать её на Postgres в своём окружении — это делается
здесь. Сверка с `docs/03-data-model.md#миграция-0016_backfill_team_leaders-концепт` и
ADR-026 (§2 `get_first_member`, §3 команды без участников, §5 инвариант «лидер ∈ участники»).

Нормативное правило backfill (идемпотентно):
  для каждой команды с `leader_id IS NULL` И непустым составом `user_teams` — назначить
  лидером первого участника по `(user_teams.created_at ASC, user_teams.user_id ASC)`;
  команды без участников остаются `leader_id=NULL` (EXISTS-гард); уже проставленный лидер
  не перезаписывается (предикат `leader_id IS NULL`); `updated_at=now()` бампится только у
  реально изменённых строк; `downgrade` — no-op.

БД предоставляет CI (`postgres:16`, `DATABASE_URL`, см. .github/workflows/ci.yml);
локально — эквивалентный одноразовый контейнер `postgres:16` с теми же кредами.

Тесты sync: миграции гоняются через `alembic.command` (env.py внутри делает
`asyncio.run`), поэтому тело теста НЕ должно жить в event loop; seed/выборка идут через
`asyncio.run(...)` над отдельным async-engine. Версия Alembic — глобальное состояние, но
pytest выполняет тесты сериями: каждый тест приводит схему к 0015, чистит домен-таблицы и
сам сеет свой датасет — изоляция без зависимости от порядка.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Реальный URL БД захватывается на импорте модуля — ДО autouse-фикстуры conftest,
# которая monkeypatch'ит DATABASE_URL на фейковый. CI задаёт job-level DATABASE_URL;
# локально экспортируется TEST_DATABASE_URL/DATABASE_URL (одноразовый postgres:16).
_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

_REV_0015 = "0015_user_first_login"
_REV_0016 = "0016_backfill_team_leaders"

# Фиксированная «дата добавления» участника (детерминизм tie-break, без зависимости от now()).
_BASE_TS = datetime(2021, 1, 1, 12, 0, 0, tzinfo=UTC)
# Заведомо прошлый `updated_at` — чтобы отличить «строку бампнули» от «не трогали».
_STALE_TS = datetime(2020, 6, 1, 8, 0, 0, tzinfo=UTC)


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return cfg


@pytest.fixture
def alembic_cfg(monkeypatch: pytest.MonkeyPatch) -> Iterator[Config]:
    """Alembic Config поверх РЕАЛЬНОГО Postgres.

    env.py читает URL из `get_settings().database_url` (conftest подменил его на фейк для
    DB-less тестов). Здесь возвращаем реальный URL и сбрасываем кэш настроек, чтобы
    `command.upgrade/downgrade` шли в настоящую БД.
    """
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — тест миграции 0016 требует реального "
            "Postgres (CI поднимает postgres:16; локально — контейнер с теми же кредами)."
        )
    monkeypatch.setenv("DATABASE_URL", _DB_URL)

    from app.config import get_settings

    get_settings.cache_clear()
    yield _alembic_config()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _restore_head_after_test(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Восстанавливает схему до head после каждого теста модуля (изоляция, ADR-038 §2).

    Тесты оставляют схему на ревизии 0016 (backfill под тестом). Без восстановления
    последующие create_all-тесты (idle `teams` с колонкой `mail_group_id` из миграции
    0018) падают на её отсутствии — ordering-зависимость от порядка коллекции. Финализатор
    дропает public-схему и накатывает head: чистый старт с полной моделью для следующих.
    """
    yield
    if not _DB_URL:
        return
    monkeypatch.setenv("DATABASE_URL", _DB_URL)
    from app.config import get_settings

    get_settings.cache_clear()
    asyncio.run(_drop_schema(_DB_URL))
    command.upgrade(_alembic_config(), "head")
    get_settings.cache_clear()


# --------------------------------------------------------------------------- helpers


async def _truncate(url: str) -> None:
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE user_teams, teams, users, roles RESTART IDENTITY CASCADE")
            )
    finally:
        await engine.dispose()


async def _drop_schema(url: str) -> None:
    """Сбрасывает public-схему целиком (и маркер alembic_version).

    Другие integration-тесты создают таблицы через `Base.metadata.create_all` БЕЗ
    stamp alembic_version. Если такой тест отработал раньше по порядку коллекции,
    последующий `upgrade head` из base упал бы на `CREATE TABLE ... already exists`.
    Полный drop гарантирует чистый старт alembic независимо от порядка тестов.
    """
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


def _reset_to_0015(cfg: Config, url: str) -> None:
    """Приводит схему к ревизии 0015 с пустыми домен-таблицами (чистый старт для seed).

    Полный drop public-схемы (изоляция от create_all-тестов без alembic-stamp) →
    `upgrade head` создаёт все таблицы alembic'ом → `truncate` очищает домен →
    `downgrade 0015` откатывает маркер на 0015 (шаг 0016→0015 — no-op по данным) —
    так следующий `upgrade 0016` реально исполнит backfill.
    """
    asyncio.run(_drop_schema(url))
    command.upgrade(cfg, "head")
    asyncio.run(_truncate(url))
    command.downgrade(cfg, _REV_0015)


async def _seed(
    url: str,
    *,
    users: list[tuple[uuid.UUID, str]],
    teams: list[tuple[uuid.UUID, str, uuid.UUID | None, datetime]],
    memberships: list[tuple[uuid.UUID, uuid.UUID, datetime]],
) -> None:
    """Сеет роль + пользователей + команды + членства с явными created_at/updated_at.

    teams: (team_id, name, leader_id|None, updated_at). memberships: (team_id, user_id, created_at).
    """
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            role_id = uuid.uuid4()
            await conn.execute(
                text("INSERT INTO roles (id, name, permissions) VALUES (:id, :n, '{}'::jsonb)"),
                {"id": role_id, "n": f"роль-{role_id.hex[:8]}"},
            )
            for uid, uname in users:
                await conn.execute(
                    text(
                        "INSERT INTO users (id, username, role_id, password_hash, is_active) "
                        "VALUES (:id, :u, :r, 'x', true)"
                    ),
                    {"id": uid, "u": uname, "r": role_id},
                )
            for tid, name, leader, upd in teams:
                await conn.execute(
                    text(
                        "INSERT INTO teams (id, name, leader_id, updated_at) "
                        "VALUES (:id, :n, :l, :upd)"
                    ),
                    {"id": tid, "n": name, "l": leader, "upd": upd},
                )
            for tid, uid, created in memberships:
                await conn.execute(
                    text(
                        "INSERT INTO user_teams (user_id, team_id, created_at) "
                        "VALUES (:u, :t, :c)"
                    ),
                    {"u": uid, "t": tid, "c": created},
                )
    finally:
        await engine.dispose()


async def _fetch(
    url: str, team_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[uuid.UUID | None, datetime]]:
    """Возвращает {team_id: (leader_id, updated_at)} для заданных команд."""
    engine = create_async_engine(url, future=True)
    try:
        async with engine.connect() as conn:
            out: dict[uuid.UUID, tuple[uuid.UUID | None, datetime]] = {}
            for tid in team_ids:
                row = (
                    await conn.execute(
                        text("SELECT leader_id, updated_at FROM teams WHERE id = :id"),
                        {"id": tid},
                    )
                ).one()
                out[tid] = (row.leader_id, row.updated_at)
            return out
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- offline (chain)


def test_0016_sits_on_top_of_0015_single_head() -> None:
    """Ревизия 0016 висит поверх 0015 и является единственной головой цепочки."""
    script = ScriptDirectory.from_config(_alembic_config())
    # Единственная голова цепочки — теперь 0018 (ADR-038 добавил teams.mail_group_id поверх 0017).
    assert script.get_heads() == ["0020_backends_domain_canon"]
    rev = script.get_revision(_REV_0016)
    assert rev.down_revision == _REV_0015


# --------------------------------------------------------------------------- backfill (real DB)


def test_backfill_assigns_first_member_by_created_at(alembic_cfg: Config) -> None:
    """Команда без лидера с ≥1 участником → лидером становится первый по created_at ASC."""
    _reset_to_0015(alembic_cfg, _DB_URL)
    team_id = uuid.uuid4()
    early_id, late_id = uuid.uuid4(), uuid.uuid4()
    asyncio.run(
        _seed(
            _DB_URL,
            users=[(early_id, f"u-{early_id.hex[:8]}"), (late_id, f"u-{late_id.hex[:8]}")],
            teams=[(team_id, "Команда Ивана", None, _STALE_TS)],
            # late добавлен РАНЬШЕ по времени вставки, но created_at у него позже →
            # порядок определяется created_at, а не порядком строк.
            memberships=[
                (team_id, late_id, _BASE_TS + timedelta(minutes=10)),
                (team_id, early_id, _BASE_TS),
            ],
        )
    )
    command.upgrade(alembic_cfg, _REV_0016)

    leader_id, updated_at = asyncio.run(_fetch(_DB_URL, [team_id]))[team_id]
    assert leader_id == early_id  # первый по created_at ASC
    assert updated_at > _STALE_TS  # строка изменена → updated_at бампнут


def test_backfill_tiebreak_by_user_id_on_equal_created_at(alembic_cfg: Config) -> None:
    """При РАВНЫХ created_at tie-break детерминирован по user_id ASC."""
    _reset_to_0015(alembic_cfg, _DB_URL)
    team_id = uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    lower_id = min(u1, u2)  # uuid.UUID сравнивается по int (big-endian) == порядок Postgres
    higher_id = max(u1, u2)
    same_ts = _BASE_TS + timedelta(hours=3)
    asyncio.run(
        _seed(
            _DB_URL,
            users=[(u1, f"u-{u1.hex[:8]}"), (u2, f"u-{u2.hex[:8]}")],
            teams=[(team_id, "Команда-тайбрейк", None, _STALE_TS)],
            memberships=[
                (team_id, higher_id, same_ts),  # больший user_id вставлен первым
                (team_id, lower_id, same_ts),
            ],
        )
    )
    command.upgrade(alembic_cfg, _REV_0016)

    leader_id, _ = asyncio.run(_fetch(_DB_URL, [team_id]))[team_id]
    assert leader_id == lower_id  # tie-break: меньший user_id


def test_backfill_skips_empty_team(alembic_cfg: Config) -> None:
    """Пустая команда без участников остаётся leader_id=NULL (EXISTS-гард), updated_at не тронут."""
    _reset_to_0015(alembic_cfg, _DB_URL)
    team_id = uuid.uuid4()
    asyncio.run(
        _seed(
            _DB_URL,
            users=[],
            teams=[(team_id, "Пустая команда", None, _STALE_TS)],
            memberships=[],
        )
    )
    command.upgrade(alembic_cfg, _REV_0016)

    leader_id, updated_at = asyncio.run(_fetch(_DB_URL, [team_id]))[team_id]
    assert leader_id is None
    assert updated_at == _STALE_TS  # не изменена → updated_at прежний


def test_backfill_does_not_overwrite_existing_leader(alembic_cfg: Config) -> None:
    """Команда с уже заданным leader_id не перезаписывается (предикат leader_id IS NULL)."""
    _reset_to_0015(alembic_cfg, _DB_URL)
    team_id = uuid.uuid4()
    leader, other = uuid.uuid4(), uuid.uuid4()
    asyncio.run(
        _seed(
            _DB_URL,
            users=[(leader, f"u-{leader.hex[:8]}"), (other, f"u-{other.hex[:8]}")],
            teams=[(team_id, "Команда с лидером", leader, _STALE_TS)],
            # `other` добавлен раньше по created_at — если бы предикат не работал, лидером
            # стал бы он; проверяем, что действующий лидер сохранён.
            memberships=[
                (team_id, other, _BASE_TS),
                (team_id, leader, _BASE_TS + timedelta(minutes=5)),
            ],
        )
    )
    command.upgrade(alembic_cfg, _REV_0016)

    leader_id, updated_at = asyncio.run(_fetch(_DB_URL, [team_id]))[team_id]
    assert leader_id == leader  # не перезаписан на `other`
    assert updated_at == _STALE_TS  # строка не тронута


def test_backfill_bumps_updated_at_only_for_changed_rows(alembic_cfg: Config) -> None:
    """updated_at бампится ТОЛЬКО у изменённых строк; нетронутые сохраняют прежний updated_at."""
    _reset_to_0015(alembic_cfg, _DB_URL)
    changed, with_leader, empty = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    m1, m2 = uuid.uuid4(), uuid.uuid4()
    asyncio.run(
        _seed(
            _DB_URL,
            users=[(m1, f"u-{m1.hex[:8]}"), (m2, f"u-{m2.hex[:8]}")],
            teams=[
                (changed, "Изменяемая", None, _STALE_TS),  # backfill проставит лидера
                (with_leader, "С лидером", m2, _STALE_TS),  # пропущена (лидер задан)
                (empty, "Пустая", None, _STALE_TS),  # пропущена (нет участников)
            ],
            memberships=[
                (changed, m1, _BASE_TS),
                (with_leader, m2, _BASE_TS),
            ],
        )
    )
    command.upgrade(alembic_cfg, _REV_0016)

    rows = asyncio.run(_fetch(_DB_URL, [changed, with_leader, empty]))
    assert rows[changed][0] == m1 and rows[changed][1] > _STALE_TS  # изменена → бамп
    assert rows[with_leader][0] == m2 and rows[with_leader][1] == _STALE_TS  # не тронута
    assert rows[empty][0] is None and rows[empty][1] == _STALE_TS  # не тронута


def test_backfill_is_idempotent_on_reapply(alembic_cfg: Config) -> None:
    """Повторный прогон backfill ничего не меняет: лидер и updated_at строки стабильны."""
    _reset_to_0015(alembic_cfg, _DB_URL)
    team_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    asyncio.run(
        _seed(
            _DB_URL,
            users=[(first, f"u-{first.hex[:8]}"), (second, f"u-{second.hex[:8]}")],
            teams=[(team_id, "Команда идемпотентности", None, _STALE_TS)],
            memberships=[
                (team_id, first, _BASE_TS),
                (team_id, second, _BASE_TS + timedelta(minutes=1)),
            ],
        )
    )
    command.upgrade(alembic_cfg, _REV_0016)
    leader_1, updated_1 = asyncio.run(_fetch(_DB_URL, [team_id]))[team_id]
    assert leader_1 == first

    # Повторный прогон: downgrade 0016→0015 (no-op по данным), затем снова upgrade 0016.
    # Предикат `leader_id IS NULL` теперь ложен для этой строки → UPDATE её не трогает.
    command.downgrade(alembic_cfg, _REV_0015)
    command.upgrade(alembic_cfg, _REV_0016)

    leader_2, updated_2 = asyncio.run(_fetch(_DB_URL, [team_id]))[team_id]
    assert leader_2 == first  # лидер стабилен
    assert updated_2 == updated_1  # updated_at не перебампнут на повторе (строка не менялась)


def test_downgrade_0016_is_noop_and_preserves_data(alembic_cfg: Config) -> None:
    """downgrade -1 (0016→0015) успешен, no-op: проставленный лидер сохраняется."""
    _reset_to_0015(alembic_cfg, _DB_URL)
    team_id = uuid.uuid4()
    member = uuid.uuid4()
    asyncio.run(
        _seed(
            _DB_URL,
            users=[(member, f"u-{member.hex[:8]}")],
            teams=[(team_id, "Команда downgrade", None, _STALE_TS)],
            memberships=[(team_id, member, _BASE_TS)],
        )
    )
    command.upgrade(alembic_cfg, _REV_0016)
    assert asyncio.run(_fetch(_DB_URL, [team_id]))[team_id][0] == member

    command.downgrade(alembic_cfg, "-1")  # no-op, не должен падать
    # Лидер НЕ откатывается в NULL — прежнее ошибочное состояние не восстанавливается.
    assert asyncio.run(_fetch(_DB_URL, [team_id]))[team_id][0] == member
