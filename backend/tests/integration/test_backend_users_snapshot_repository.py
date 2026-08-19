"""Регресс-гейты репозитория снимка «Юзеров бэков» на реальном Postgres (ADR-080 §2/§3).

По одному гейту на дефект, найденный ревью:

- `delete_rows` чанкует `IN`-список и не упирается в потолок asyncpg (32 767
  bind-параметров) — прежняя редакция строила `NOT IN (все увиденные id)` и на бэке с
  сотнями тысяч пользователей падала КАЖДЫЙ цикл, из-за чего `refreshed_at` не
  проставлялся бы никогда;
- поиск экранирует метасимволы LIKE (`%`, `_`, `\\`) — иначе `%` матчил бы все строки.

Полное сценарное покрытие (пагинация, tie-break, stats, errors, api_costs) — зона qa.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from app.models.service_backend import Backend
from app.repositories import backend_user_snapshot_repository as repo_module
from app.repositories.backend_user_snapshot_repository import BackendUserSnapshotRepository
from mail_s34_helpers import mail_db
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


@asynccontextmanager
async def _snapshot_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """БД со снимком: чистые таблицы снимка + один бэк-владелец строк."""
    async with mail_db() as sm:
        async with sm() as session:
            await session.execute(
                sa_text(
                    "TRUNCATE backend_user_snapshots, backend_user_snapshot_sources, "
                    "backends RESTART IDENTITY CASCADE"
                )
            )
            session.add(
                Backend(
                    id=BACKEND_ID,
                    code="veltrio",
                    name="232",
                    domain="https://veltriohub.shop/",
                )
            )
            await session.commit()
        yield sm


async def _seed_rows(sm: async_sessionmaker[AsyncSession], user_ids: list[str]) -> None:
    async with sm() as session:
        repo = BackendUserSnapshotRepository(session)
        registered_at = datetime(2026, 8, 13, 17, 1, 14, tzinfo=UTC)
        for start in range(0, len(user_ids), 500):
            await repo.upsert_rows(
                [
                    {
                        "backend_id": BACKEND_ID,
                        "user_id": user_id,
                        "external_id": f"ext-{user_id}",
                        "registered_at": registered_at,
                    }
                    for user_id in user_ids[start : start + 500]
                ]
            )
        await session.commit()


async def _remaining(sm: async_sessionmaker[AsyncSession]) -> set[str]:
    async with sm() as session:
        rows = await session.execute(
            sa_text("SELECT user_id FROM backend_user_snapshots WHERE backend_id = :b"),
            {"b": str(BACKEND_ID)},
        )
        return set(rows.scalars().all())


async def test_delete_rows_chunks_beyond_bind_parameter_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Гейт: список удаляемых длиннее чанка режется на несколько statement'ов.

    Порог понижен monkeypatch'ем — гейт проверяет САМ чанкинг, а не воспроизводит
    33 000 строк (это стоило бы минуты на каждый прогон). Прежняя редакция слала один
    `NOT IN` со всеми id и на реальном объёме падала бы `too many bind parameters`.
    """
    monkeypatch.setattr(repo_module, "_DELETE_CHUNK", 100)
    user_ids = [f"u{i:05d}" for i in range(450)]

    async with _snapshot_db() as sm:
        await _seed_rows(sm, user_ids)

        async with sm() as session:
            deleted = await BackendUserSnapshotRepository(session).delete_rows(
                BACKEND_ID, user_ids[:420]
            )
            await session.commit()

        assert deleted == 420
        assert await _remaining(sm) == set(user_ids[420:])


async def test_delete_rows_empty_input_is_noop() -> None:
    """Пустая разность (установившийся режим) — ни одного statement'а и ни одной потери."""
    async with _snapshot_db() as sm:
        await _seed_rows(sm, ["u1", "u2"])

        async with sm() as session:
            deleted = await BackendUserSnapshotRepository(session).delete_rows(BACKEND_ID, [])
            await session.commit()

        assert deleted == 0
        assert await _remaining(sm) == {"u1", "u2"}


async def test_search_escapes_like_wildcards() -> None:
    """Гейт: `%`/`_` из ввода — литералы, а не шаблон (ADR-080 §3: подстрочный поиск)."""
    async with _snapshot_db() as sm:
        await _seed_rows(sm, ["plain-user", "100%-user", "a_b-user"])

        async with sm() as session:
            repo = BackendUserSnapshotRepository(session)

            # `%` в запросе не должен матчить всё подряд — только строку с самим `%`.
            percent_rows, percent_total = await repo.list_page(
                backend_ids=[BACKEND_ID],
                search="100%",
                date_from=None,
                date_to=None,
                is_paid=None,
                limit=50,
                offset=0,
            )
            # `_` — литерал подчёркивания, а не «любой символ»: `a_b` не матчит `axb`.
            underscore_rows, _underscore_total = await repo.list_page(
                backend_ids=[BACKEND_ID],
                search="a_b",
                date_from=None,
                date_to=None,
                is_paid=None,
                limit=50,
                offset=0,
            )
            bare_percent_rows, _bare_total = await repo.list_page(
                backend_ids=[BACKEND_ID],
                search="%",
                date_from=None,
                date_to=None,
                is_paid=None,
                limit=50,
                offset=0,
            )

    assert percent_total == 1
    assert [row.user_id for row in percent_rows] == ["100%-user"]
    assert [row.user_id for row in underscore_rows] == ["a_b-user"]
    # Голый `%` — тоже литерал: матчится только строка, где он реально есть.
    assert [row.user_id for row in bare_percent_rows] == ["100%-user"]
