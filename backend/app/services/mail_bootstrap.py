"""Startup-посев builtin-тегов почты в lifespan (ADR-044 §5).

Идемпотентно по `UNIQUE (name)` (по образцу агрегаторского `seed_builtin_tags`/
`seed_super_admin`). На проде builtin придут миграцией данных — seed добирает
отсутствующие; на чистой установке создаёт каталог с нуля. Ленивого per-login-хука
почты в CRM нет.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.mail_builtin_tags import BUILTIN_TAGS
from app.logging import get_logger
from app.repositories.mail_tag_repository import MailTagRepository

logger = get_logger(__name__)


async def seed_builtin_mail_tags(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Создать отсутствующие builtin-теги почты; вернуть число созданных."""
    async with sessionmaker() as session, session.begin():
        created = await MailTagRepository(session).seed_builtin(BUILTIN_TAGS)
    if created:
        logger.info("mail_builtin_tags_seeded", created=created)
    return created
