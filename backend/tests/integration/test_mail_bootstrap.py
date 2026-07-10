"""Integration-тесты посева builtin-тегов почты в lifespan (ADR-044 §5).

`seed_builtin_mail_tags` идемпотентен по `UNIQUE (name)`: первый прогон создаёт весь
канонический набор (10 глобальных builtin-тегов с правилами), повтор — 0 создаёт, дублей
нет. Против реального Postgres (без FastAPI-app).
"""

from __future__ import annotations

from app.domain.mail_builtin_tags import BUILTIN_TAGS
from app.models.mail_tag import MailTag, MailTagRule
from app.services.mail_bootstrap import seed_builtin_mail_tags
from mail_helpers import mail_db
from sqlalchemy import func, select

_EXPECTED_COUNT = 10


def test_catalogue_has_ten_builtin_tags() -> None:
    """Канонический набор — ровно 10 тегов (порт из агрегатора)."""
    assert len(BUILTIN_TAGS) == _EXPECTED_COUNT
    # Имена уникальны (иначе UNIQUE(name) отбросил бы часть при посеве).
    assert len({t["name"] for t in BUILTIN_TAGS}) == _EXPECTED_COUNT


async def test_seed_creates_all_builtin_tags_with_rules() -> None:
    async with mail_db() as sm:
        created = await seed_builtin_mail_tags(sm)
        assert created == _EXPECTED_COUNT
        async with sm() as s:
            total = (await s.execute(select(func.count()).select_from(MailTag))).scalar_one()
            builtin = (
                await s.execute(
                    select(func.count()).select_from(MailTag).where(MailTag.is_builtin.is_(True))
                )
            ).scalar_one()
            rules = (await s.execute(select(func.count()).select_from(MailTagRule))).scalar_one()
            assert total == _EXPECTED_COUNT
            assert builtin == _EXPECTED_COUNT
            expected_rules = sum(len(t["rules"]) for t in BUILTIN_TAGS)
            assert rules == expected_rules


async def test_seed_is_idempotent_no_duplicates() -> None:
    async with mail_db() as sm:
        first = await seed_builtin_mail_tags(sm)
        second = await seed_builtin_mail_tags(sm)
        assert first == _EXPECTED_COUNT
        assert second == 0  # повтор ничего не создаёт (UNIQUE name)
        async with sm() as s:
            total = (await s.execute(select(func.count()).select_from(MailTag))).scalar_one()
            assert total == _EXPECTED_COUNT
