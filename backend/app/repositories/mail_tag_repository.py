"""Репозиторий тегов почты (движок матчинга + seed builtin, ADR-044 §5).

`apply_tags_to_message` — применить все матчащие теги к одному письму (на приёме
push'а, синхронно). `apply_tag_to_existing` — bulk-INSERT правил тега по всем письмам
(apply-to-existing). Обе — через ПОБУКВЕННО портированный SQL (`mail_tags_sql`), без
visibility-веток (теги глобальны). `seed_builtin` — идемпотентный посев builtin по
`UNIQUE (name)` в lifespan.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.mail_builtin_tags import BuiltinTag
from app.domain.mail_tags_sql import APPLY_TAG_TO_EXISTING, APPLY_TAGS_TO_MESSAGE
from app.models.mail_tag import MailMessageTag, MailTag, MailTagRule


class MailTagRepository:
    """Движок применения тегов + посев builtin + CRUD глобального каталога (§5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- Чтение каталога (GET /tags, лента) --------------------------------

    async def list_tags(self) -> list[MailTag]:
        """Все теги каталога, сортировка `name ASC` (GET /tags, ADR-044 §5)."""
        stmt = select(MailTag).order_by(MailTag.name.asc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def get(self, tag_id: uuid.UUID) -> MailTag | None:
        """Тег по id или None."""
        return await self._session.get(MailTag, tag_id)

    async def rules_for_tags(
        self, tag_ids: Iterable[uuid.UUID]
    ) -> dict[uuid.UUID, list[MailTagRule]]:
        """Правила тегов, сгруппированные по `tag_id` (для `MailTagFull.rules`)."""
        ids = list(tag_ids)
        if not ids:
            return {}
        stmt = (
            select(MailTagRule)
            .where(MailTagRule.tag_id.in_(ids))
            .order_by(MailTagRule.created_at.asc(), MailTagRule.id.asc())
        )
        grouped: dict[uuid.UUID, list[MailTagRule]] = {}
        for rule in (await self._session.execute(stmt)).scalars().all():
            grouped.setdefault(rule.tag_id, []).append(rule)
        return grouped

    async def tags_for_messages(self, message_ids: Iterable[int]) -> dict[int, list[MailTag]]:
        """Теги писем, сгруппированные по `message_id` (для ленты, ADR-044 §2)."""
        ids = list(message_ids)
        if not ids:
            return {}
        stmt = (
            select(MailMessageTag.message_id, MailTag)
            .join(MailTag, MailTag.id == MailMessageTag.tag_id)
            .where(MailMessageTag.message_id.in_(ids))
            .order_by(MailTag.name.asc())
        )
        grouped: dict[int, list[MailTag]] = {}
        for message_id, tag in (await self._session.execute(stmt)).all():
            grouped.setdefault(message_id, []).append(tag)
        return grouped

    # --- CRUD тегов и правил (mail:tags) -----------------------------------

    async def exists_by_name(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        """Занято ли имя тега (глобальный `UNIQUE (name)` → 409 mail_conflict)."""
        stmt = select(MailTag.id).where(MailTag.name == name)
        if exclude_id is not None:
            stmt = stmt.where(MailTag.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).first() is not None

    async def create_tag(self, *, name: str, color: str, match_mode: str) -> MailTag:
        """Создать тег (без правил; `is_builtin=false`, ADR-044 §5)."""
        tag = MailTag(name=name, color=color, match_mode=match_mode, is_builtin=False)
        self._session.add(tag)
        await self._session.flush()
        return tag

    async def delete_tag(self, tag_id: uuid.UUID) -> None:
        """Удалить тег (CASCADE удалит правила и связи письмо↔тег, ADR-044 §5)."""
        await self._session.execute(sa_delete(MailTag).where(MailTag.id == tag_id))

    async def get_rule(self, tag_id: uuid.UUID, rule_id: uuid.UUID) -> MailTagRule | None:
        """Правило тега по составному ключу (валидация принадлежности) или None."""
        stmt = select(MailTagRule).where(MailTagRule.id == rule_id, MailTagRule.tag_id == tag_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_rule(self, *, tag_id: uuid.UUID, rule_type: str, pattern: str) -> MailTagRule:
        """Добавить правило тегу (ADR-044 §5)."""
        rule = MailTagRule(tag_id=tag_id, type=rule_type, pattern=pattern)
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def delete_rule(self, tag_id: uuid.UUID, rule_id: uuid.UUID) -> None:
        """Удалить правило тега (по составному ключу, ADR-044 §5)."""
        await self._session.execute(
            sa_delete(MailTagRule).where(MailTagRule.id == rule_id, MailTagRule.tag_id == tag_id)
        )

    async def apply_tags_to_message(
        self,
        *,
        message_id: int,
        subject: str | None,
        body_text: str,
        body_html: str | None,
        from_addr: str,
        from_name: str | None,
    ) -> None:
        """Применить все матчащие глобальные теги к письму (на приёме push'а).

        `:subject` без CAST в SQL → None заменяется на `""` (пустая строка не матчит
        whole-word; asyncpg не выводит тип для голого NULL-бинда). `:body_html`/
        `:sender_name` — с CAST, None безопасен.
        """
        await self._session.execute(
            text(APPLY_TAGS_TO_MESSAGE),
            {
                "message_id": message_id,
                "subject": subject or "",
                "body": body_text,
                "body_html": body_html,
                "sender": from_addr,
                "sender_name": from_name,
            },
        )

    async def apply_tag_to_existing(self, tag_id: uuid.UUID) -> int:
        """Применить правила тега ко ВСЕМ письмам (bulk); вернуть число новых связей."""
        result = await self._session.execute(text(APPLY_TAG_TO_EXISTING), {"tag_id": tag_id})
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def seed_builtin(self, catalogue: Sequence[BuiltinTag]) -> int:
        """Идемпотентно создать отсутствующие builtin-теги (по `UNIQUE (name)`).

        Существующий по имени тег НЕ трогается (`ON CONFLICT (name) DO NOTHING`);
        правила добавляются только новосозданным. Возвращает число созданных тегов.
        """
        created = 0
        for tag in catalogue:
            stmt = (
                pg_insert(MailTag)
                .values(
                    name=tag["name"],
                    color=tag["color"],
                    match_mode=tag["match_mode"],
                    is_builtin=True,
                )
                .on_conflict_do_nothing(index_elements=["name"])
                .returning(MailTag.id)
            )
            tag_id = (await self._session.execute(stmt)).scalar_one_or_none()
            if tag_id is None:
                continue  # тег с таким именем уже есть — не трогаем
            created += 1
            for rule in tag["rules"]:
                await self._session.execute(
                    pg_insert(MailTagRule).values(
                        tag_id=tag_id,
                        type=rule["type"],
                        pattern=rule["pattern"],
                    )
                )
        return created
