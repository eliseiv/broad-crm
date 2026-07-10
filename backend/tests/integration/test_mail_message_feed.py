"""Integration-тесты компаундного keyset-листинга ленты писем (ADR-044 §2, MINOR-2).

Ключевой тест: несколько писем с **одинаковым** `internal_date` — пагинация не
пропускает и не дублирует на границе страницы. Односоставный курсор (только по
`internal_date`) здесь ломается (пропуск писем той же секунды на стыке страниц);
компаундный `(internal_date, id)` — обязан покрывать. Плюс фильтр по ящикам и
анти-энумерация (пустой набор → без запроса).
"""

from __future__ import annotations

from datetime import datetime

from app.models.mail_message import MailMessage
from app.repositories.mail_message_repository import MailMessageRepository
from mail_helpers import dt, mail_db, seed_account
from sqlalchemy.ext.asyncio import AsyncSession


async def _add(session: AsyncSession, *, account_id: int, uid: int, when: datetime) -> MailMessage:
    msg = MailMessage(
        mail_account_id=account_id,
        uidvalidity=1,
        uid=uid,
        from_addr="s@e.com",
        to_addrs="",
        internal_date=when,
        body_text="x",
    )
    session.add(msg)
    await session.flush()
    return msg


async def _paginate_all(
    repo: MailMessageRepository, *, account_ids: list[int] | None, page_size: int
) -> list[int]:
    """Полный обход ленты keyset'ом; возвращает id в порядке выдачи."""
    collected: list[int] = []
    cursor: tuple[datetime, int] | None = None
    while True:
        page = await repo.list_feed(mail_account_ids=account_ids, cursor=cursor, limit=page_size)
        collected.extend(m.id for m in page)
        if len(page) < page_size:
            break
        last = page[-1]
        cursor = (last.internal_date, last.id)
    return collected


async def test_keyset_no_skip_or_dup_with_identical_internal_date() -> None:
    """Границы страниц проходят ВНУТРИ одной и той же секунды — компаунд обязан держать."""
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=1)
        d1 = dt(2026, 7, 1, 12, 0)
        d2 = dt(2026, 7, 2, 12, 0)  # новее
        # 3 письма одной секунды d1 + 2 письма одной секунды d2 (id по порядку вставки).
        m1 = await _add(session, account_id=1, uid=1, when=d1)
        m2 = await _add(session, account_id=1, uid=2, when=d1)
        m3 = await _add(session, account_id=1, uid=3, when=d1)
        m4 = await _add(session, account_id=1, uid=4, when=d2)
        m5 = await _add(session, account_id=1, uid=5, when=d2)

        repo = MailMessageRepository(session)
        order = await _paginate_all(repo, account_ids=[1], page_size=2)

        # Ожидаемый порядок: internal_date DESC, id DESC → d2(5,4), затем d1(3,2,1).
        assert order == [m5.id, m4.id, m3.id, m2.id, m1.id]
        # Ни пропусков, ни дублей: множество полное и без повторов.
        assert len(order) == len(set(order)) == 5


async def test_feed_filters_by_account_ids() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=1)
        await seed_account(session, account_id=2)
        mine = await _add(session, account_id=1, uid=1, when=dt(2026, 7, 1))
        await _add(session, account_id=2, uid=1, when=dt(2026, 7, 2))
        repo = MailMessageRepository(session)
        page = await repo.list_feed(mail_account_ids=[1], cursor=None, limit=10)
        assert [m.id for m in page] == [mine.id]


async def test_feed_empty_account_set_returns_empty_without_query() -> None:
    async with mail_db() as sm, sm() as session:
        await seed_account(session, account_id=1)
        await _add(session, account_id=1, uid=1, when=dt(2026, 7, 1))
        repo = MailMessageRepository(session)
        # Пустой список видимых ящиков → пусто (анти-энумерация), None → все.
        assert await repo.list_feed(mail_account_ids=[], cursor=None, limit=10) == []
        assert len(await repo.list_feed(mail_account_ids=None, cursor=None, limit=10)) == 1
