"""Tie-break пагинации снимка «Юзеров бэков» (ADR-080 §3, 06-testing-strategy.md стр. 213).

Сортировка списка — `registered_at DESC, backend_id, user_id`. Tie-break **обязателен**:
окно merge ≤ 1000 упразднено, глубина `LIMIT/OFFSET` ничем не ограничена, и при равных
`registered_at` (массовый импорт, миграция бэка — обычное дело) Postgres волен вернуть
строки в любом порядке. Тогда постраничный проход даёт классический дефект: одна строка
приходит на двух страницах, другая не приходит ни на одной — оператор ищет пользователя,
которого «нет», хотя он в снимке есть.

Фикстура специально устроена так, что **порядок по `user_id` не совпадает с нормативным**:
у бэка с меньшим `backend_id` лежат `u-b`/`u-z`, у бэка с большим — `u-a`. Реализация,
где tie-break сделан только по `user_id`, вернула бы `u-a` первым и гейт упадёт.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from app.models.service_backend import Backend
from app.repositories.backend_user_snapshot_repository import BackendUserSnapshotRepository
from mail_s34_helpers import mail_db
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# `LOW_ID < HIGH_ID` в сравнении uuid — нормативный первый tie-break.
LOW_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c8")
HIGH_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c9")

# ОДИН И ТОТ ЖЕ момент регистрации у всех строк — иначе tie-break не проверяется вовсе.
SAME_MOMENT = datetime(2026, 8, 13, 17, 1, 14, tzinfo=UTC)

# Нормативный порядок: сперва бэк с меньшим `backend_id`, внутри бэка — по `user_id`.
EXPECTED: list[tuple[uuid.UUID, str]] = [
    (LOW_ID, "u-b"),
    (LOW_ID, "u-z"),
    (HIGH_ID, "u-a"),
]


@asynccontextmanager
async def _snapshot_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Два бэка и три строки снимка с ОДИНАКОВЫМ `registered_at`."""
    async with mail_db() as sm:
        async with sm() as session:
            await session.execute(
                sa_text(
                    "TRUNCATE backend_user_snapshots, backend_user_snapshot_sources, "
                    "backends RESTART IDENTITY CASCADE"
                )
            )
            for backend_id, code in ((LOW_ID, "veltrio"), (HIGH_ID, "selquro")):
                session.add(
                    Backend(
                        id=backend_id,
                        code=code,
                        name=code.title(),
                        domain=f"https://{code}.shop/",
                    )
                )
            await session.commit()

        async with sm() as session:
            # Порядок вставки НАМЕРЕННО обратный ожидаемому: физический порядок строк не
            # должен подменять собой `ORDER BY`.
            await BackendUserSnapshotRepository(session).upsert_rows(
                [
                    {
                        "backend_id": backend_id,
                        "user_id": user_id,
                        "external_id": f"ext-{user_id}",
                        "registered_at": SAME_MOMENT,
                    }
                    for backend_id, user_id in reversed(EXPECTED)
                ]
            )
            await session.commit()
        yield sm


async def _page(
    sm: async_sessionmaker[AsyncSession], *, limit: int, offset: int
) -> tuple[list[tuple[uuid.UUID, str]], int]:
    async with sm() as session:
        rows, total = await BackendUserSnapshotRepository(session).list_page(
            backend_ids=[LOW_ID, HIGH_ID],
            search=None,
            date_from=None,
            date_to=None,
            is_paid=None,
            limit=limit,
            offset=offset,
        )
    return [(row.backend_id, row.user_id) for row in rows], total


async def test_equal_registered_at_orders_by_backend_id_then_user_id() -> None:
    """Гейт: при равном `registered_at` порядок детерминирован по `(backend_id, user_id)`."""
    async with _snapshot_db() as sm:
        first, total = await _page(sm, limit=50, offset=0)
        repeated, _total = await _page(sm, limit=50, offset=0)

    assert total == 3
    assert first == EXPECTED
    # Повторный запрос обязан дать тот же порядок — иначе пагинация нестабильна.
    assert repeated == EXPECTED


async def test_limit_one_walk_covers_every_row_without_repeats_or_gaps() -> None:
    """Постраничный проход `limit=1` покрывает все строки — без повторов и пропусков.

    Именно этот проход ломается при отсутствии tie-break: одна строка выпадает, другая
    дублируется, а `total` при этом честно говорит «три».
    """
    walked: list[tuple[uuid.UUID, str]] = []
    async with _snapshot_db() as sm:
        for offset in range(3):
            rows, total = await _page(sm, limit=1, offset=offset)
            assert total == 3
            assert len(rows) == 1
            walked.extend(rows)
        tail, _total = await _page(sm, limit=1, offset=3)

    assert walked == EXPECTED
    assert len(set(walked)) == 3  # ни одного повтора
    assert tail == []  # за последней строкой — пусто
