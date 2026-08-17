"""Репозиторий `knowledge_bot_links` (modules/broadcast, ADR-076).

Upsert по `telegram_user_id`, EXISTS для `bot_started`, адресаты рассылки
(активные несистемные ∩ роли ∩ живые линки, дедуп по chat_id), счётчики аудитории.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_bot_link import KnowledgeBotLink
from app.models.role import Role
from app.models.user import User


@dataclass(frozen=True, slots=True)
class KnowledgeBotRecipient:
    """Адресат рассылки: chat_id живой привязки ИИ-бота."""

    telegram_user_id: int


@dataclass(frozen=True, slots=True)
class RoleAudienceCounts:
    """Счётчики запуска бота по одной роли (активные несистемные)."""

    role_id: uuid.UUID
    name: str
    started_count: int
    not_started_count: int


class KnowledgeBotLinkRepository:
    """Upsert/статус линков ИИ-бота + адресаты и счётчики аудитории."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, *, telegram_user_id: int, user_id: uuid.UUID, username: str | None
    ) -> KnowledgeBotLink:
        """Идемпотентная привязка (`ON CONFLICT DO UPDATE`). `started_at` не затирается."""
        stmt = (
            pg_insert(KnowledgeBotLink)
            .values(
                telegram_user_id=telegram_user_id,
                user_id=user_id,
                username=username,
            )
            .on_conflict_do_update(
                index_elements=[KnowledgeBotLink.telegram_user_id],
                set_={
                    "user_id": user_id,
                    "username": username,
                    "dead_at": None,
                },
            )
            .returning(KnowledgeBotLink)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def get_active_by_telegram_user_id(
        self, telegram_user_id: int
    ) -> KnowledgeBotLink | None:
        """Живая привязка (`dead_at IS NULL`) по chat_id или None."""
        stmt = select(KnowledgeBotLink).where(
            KnowledgeBotLink.telegram_user_id == telegram_user_id,
            KnowledgeBotLink.dead_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def exists_for_user(self, user_id: uuid.UUID) -> bool:
        """True ⇔ есть хотя бы одна активная строка на этого пользователя."""
        stmt = select(
            exists().where(
                KnowledgeBotLink.user_id == user_id,
                KnowledgeBotLink.dead_at.is_(None),
            )
        )
        return bool((await self._session.execute(stmt)).scalar())

    async def active_user_ids(self, user_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Подмножество `user_ids` с хотя бы одним активным линком (батч, без N+1)."""
        if not user_ids:
            return set()
        stmt = (
            select(KnowledgeBotLink.user_id)
            .where(
                KnowledgeBotLink.user_id.in_(user_ids),
                KnowledgeBotLink.dead_at.is_(None),
            )
            .distinct()
        )
        return set((await self._session.execute(stmt)).scalars().all())

    async def mark_dead(self, telegram_user_id: int) -> None:
        """Пометить привязку мёртвой (`403` Bot API): `dead_at = now()`."""
        await self._session.execute(
            update(KnowledgeBotLink)
            .where(
                KnowledgeBotLink.telegram_user_id == telegram_user_id,
                KnowledgeBotLink.dead_at.is_(None),
            )
            .values(dead_at=datetime.now(UTC))
        )

    async def audience_by_role(self) -> list[RoleAudienceCounts]:
        """Счётчики started/not_started по каждой роли (активные несистемные)."""
        has_link = exists().where(
            KnowledgeBotLink.user_id == User.id,
            KnowledgeBotLink.dead_at.is_(None),
        )
        started = func.count(User.id).filter(has_link)
        not_started = func.count(User.id).filter(~has_link)
        stmt = (
            select(Role.id, Role.name, started, not_started)
            .outerjoin(
                User,
                (User.role_id == Role.id) & User.is_active.is_(True) & User.is_system.is_(False),
            )
            .group_by(Role.id)
            .order_by(Role.created_at.asc(), Role.id.asc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            RoleAudienceCounts(
                role_id=role_id,
                name=name,
                started_count=int(started_count),
                not_started_count=int(not_started_count),
            )
            for role_id, name, started_count, not_started_count in rows
        ]

    async def audience_totals(self) -> tuple[int, int]:
        """`(all_started_count, all_not_started_count)` по всем активным несистемным."""
        has_link = exists().where(
            KnowledgeBotLink.user_id == User.id,
            KnowledgeBotLink.dead_at.is_(None),
        )
        stmt = select(
            func.count(User.id).filter(has_link),
            func.count(User.id).filter(~has_link),
        ).where(User.is_active.is_(True), User.is_system.is_(False))
        started, not_started = (await self._session.execute(stmt)).one()
        return int(started), int(not_started)

    async def recipients_for_roles(
        self, *, all_users: bool, role_ids: set[uuid.UUID]
    ) -> tuple[list[KnowledgeBotRecipient], int]:
        """Адресаты (уникальные chat_id) и `skipped_not_started` (кандидаты без линка).

        Кандидаты = активные несистемные (все либо `role_id ∈ role_ids`).
        Адресаты = UNIQUE(telegram_user_id) живых линков этих кандидатов.
        """
        candidates_stmt = select(User.id).where(
            User.is_active.is_(True),
            User.is_system.is_(False),
        )
        if not all_users:
            candidates_stmt = candidates_stmt.where(User.role_id.in_(role_ids))
        candidate_ids = set((await self._session.execute(candidates_stmt)).scalars().all())
        if not candidate_ids:
            return [], 0

        links_stmt = select(
            KnowledgeBotLink.telegram_user_id,
            KnowledgeBotLink.user_id,
        ).where(
            KnowledgeBotLink.dead_at.is_(None),
            KnowledgeBotLink.user_id.in_(candidate_ids),
        )
        rows = (await self._session.execute(links_stmt)).all()
        seen_chats: set[int] = set()
        recipients: list[KnowledgeBotRecipient] = []
        linked_users: set[uuid.UUID] = set()
        for telegram_user_id, user_id in rows:
            linked_users.add(user_id)
            chat_id = int(telegram_user_id)
            if chat_id in seen_chats:
                continue
            seen_chats.add(chat_id)
            recipients.append(KnowledgeBotRecipient(telegram_user_id=chat_id))
        skipped = len(candidate_ids) - len(linked_users)
        return recipients, skipped
