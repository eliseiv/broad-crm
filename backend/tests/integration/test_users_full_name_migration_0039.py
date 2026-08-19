"""Миграция `0039_users_full_name_telegram` на реальном Postgres (ADR-079 §7).

Нормативные гейты (06-testing-strategy.md § «Волна ADR-079» → Миграции):

- три колонки ФИО добавлены и **nullable** (обязательность `last_name`/`first_name` —
  прикладная, `422`, а не схемная: у существующих строк фамилии нет);
- backfill `first_name = username` **только для `is_system = false`** — у системного
  якоря ФИО осталось `NULL` (гейт против «имени `superadmin@system`» в UI);
- **`telegram` и `uq_users_telegram` НЕ изменены**: снимок колонки и всех индексов
  `users`, упоминающих `telegram`, до и после `upgrade()` совпадает, а строки с
  `telegram IS NULL` пережили миграцию (иначе она падала бы на проде);
- `downgrade()` дропает ровно три колонки ФИО;
- идентификаторы ревизии: `0039_users_full_name_telegram` / `0038_user_roles_m2m`,
  длина `revision` ≤ 32 (лимит `alembic_version.version_num`).

Способ поднятия БД — как в `test_users_roles_m2m_migration_0038.py`: реальный Postgres
через `mail_db()`, миграция грузится по пути (`alembic/versions` — не пакет).
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic.migration import MigrationContext
from alembic.operations import Operations
from mail_s34_helpers import mail_db
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0039_users_full_name_telegram.py"
)
_SPEC = importlib.util.spec_from_file_location("migration_0039", _MIGRATION_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)

_NAME_COLUMNS = ("last_name", "first_name", "middle_name")


async def _run(sm: async_sessionmaker[AsyncSession], step: str) -> None:
    """Прогоняет `upgrade()`/`downgrade()` 0039 поверх актуальной схемы."""
    async with sm() as session:
        conn = await session.connection()

        def _apply(sync_conn: object) -> None:
            context = MigrationContext.configure(sync_conn)  # type: ignore[arg-type]
            with Operations.context(context):
                getattr(_MIGRATION, step)()

        await conn.run_sync(_apply)
        await session.commit()


async def _name_columns(sm: async_sessionmaker[AsyncSession]) -> dict[str, str]:
    """`{колонка: is_nullable}` по колонкам ФИО (отсутствующие в словарь не попадают)."""
    async with sm() as session:
        rows = (
            await session.execute(
                sa_text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'users' "
                    "AND column_name IN ('last_name', 'first_name', 'middle_name')"
                )
            )
        ).all()
    return {str(name): str(nullable) for name, nullable in rows}


async def _telegram_schema_snapshot(sm: async_sessionmaker[AsyncSession]) -> tuple[Any, ...]:
    """Снимок всего, что относится к `telegram`: сама колонка + индексы `users` по ней.

    Сравнение снимков до/после — прямая проверка нормы «`telegram` и `uq_users_telegram`
    НЕ изменены». Именно проверка ИНВАРИАНТНОСТИ, а не наличия конкретного индекса:
    тестовая схема поднимается `Base.metadata.create_all`, и набор индексов зависит от
    того, накатывался ли на эту БД alembic, — а норма звучит «не изменены».
    """
    async with sm() as session:
        column = (
            await session.execute(
                sa_text(
                    "SELECT column_name, data_type, is_nullable, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'users' "
                    "AND column_name = 'telegram'"
                )
            )
        ).all()
        indexes = (
            await session.execute(
                sa_text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = 'users' "
                    "AND indexdef ILIKE '%telegram%' ORDER BY indexname"
                )
            )
        ).all()
    return (tuple(column), tuple(indexes))


@asynccontextmanager
async def _reversible_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """БД, чья схема ГАРАНТИРОВАННО возвращается к head после теста.

    Тестовая БД одна на весь прогон, а `downgrade()` физически снимает колонки ФИО:
    без обратного `upgrade()` соседние файлы получили бы схему предыдущей ревизии и
    падали бы на вставке пользователей (`Base.metadata.create_all` лишнего не чинит —
    он лишь досоздаёт недостающее, а вот в середине теста колонок уже нет).
    """
    async with mail_db() as sm:
        try:
            yield sm
        finally:
            if len(await _name_columns(sm)) < len(_NAME_COLUMNS):
                await _run(sm, "upgrade")


async def _seed_user(
    session: AsyncSession, username: str, *, telegram: str | None = None
) -> uuid.UUID:
    """Несистемный пользователь ПРЯМЫМ SQL — без колонок ФИО (их в этот момент нет)."""
    user_id = uuid.uuid4()
    await session.execute(
        sa_text(
            "INSERT INTO users (id, username, password_hash, is_active, is_system, telegram) "
            "VALUES (:id, :u, 'x', true, false, :tg)"
        ),
        {"id": str(user_id), "u": username, "tg": telegram},
    )
    return user_id


def test_migration_0039_revision_identifiers() -> None:
    """Идентификаторы ревизии и лимит длины `alembic_version.version_num` (32)."""
    assert _MIGRATION.revision == "0039_users_full_name_telegram"
    assert _MIGRATION.down_revision == "0038_user_roles_m2m"
    assert len(_MIGRATION.revision) <= 32


async def test_upgrade_0039_adds_nullable_columns_and_backfills_only_non_system() -> None:
    """Три nullable-колонки; `first_name = username` ТОЛЬКО у `is_system = false`.

    Порядок: откатываем 0039 (колонок ФИО нет — состояние «до миграции»), сеем строки
    прямым SQL, накатываем `upgrade()` и смотрим на результат backfill'а.
    """
    async with _reversible_db() as sm:
        await _run(sm, "downgrade")
        assert await _name_columns(sm) == {}

        async with sm() as session:
            await _seed_user(session, "иван_логин")  # telegram IS NULL — переживает миграцию
            await _seed_user(session, "петр_логин", telegram="petr_01")
            await session.commit()

        before = await _telegram_schema_snapshot(sm)

        await _run(sm, "upgrade")

        after = await _telegram_schema_snapshot(sm)
        columns = await _name_columns(sm)
        async with sm() as session:
            rows = (
                await session.execute(
                    sa_text(
                        "SELECT username, is_system, last_name, first_name, middle_name, telegram "
                        "FROM users ORDER BY username"
                    )
                )
            ).all()

    # (1) колонки добавлены и все три nullable.
    assert columns == dict.fromkeys(_NAME_COLUMNS, "YES")

    by_username = {str(r[0]): r for r in rows}
    # (2) backfill у несистемных: `first_name = username`, фамилия/отчество не выдуманы.
    for username in ("иван_логин", "петр_логин"):
        _, is_system, last_name, first_name, middle_name, _tg = by_username[username]
        assert is_system is False
        assert first_name == username
        assert last_name is None
        assert middle_name is None

    # (3) якорь: ФИО осталось NULL — служебный `superadmin@system` не должен стать «именем».
    anchors = [r for r in rows if r[1] is True]
    assert len(anchors) == 1
    assert (anchors[0][2], anchors[0][3], anchors[0][4]) == (None, None, None)

    # (4) `telegram`/`uq_users_telegram` не тронуты, строки с `telegram IS NULL` живы.
    assert after == before
    assert by_username["иван_логин"][5] is None
    assert by_username["петр_логин"][5] == "petr_01"


async def test_downgrade_0039_drops_three_name_columns() -> None:
    """`downgrade()` снимает ровно три колонки ФИО (lossy по введённым значениям)."""
    async with _reversible_db() as sm:
        assert await _name_columns(sm) == dict.fromkeys(_NAME_COLUMNS, "YES")

        await _run(sm, "downgrade")

        assert await _name_columns(sm) == {}
        # Строки пользователей от отката не пострадали — снят только столбцовый набор.
        async with sm() as session:
            survived = (
                await session.execute(sa_text("SELECT count(*) FROM users WHERE is_system"))
            ).scalar_one()
        assert survived == 1
