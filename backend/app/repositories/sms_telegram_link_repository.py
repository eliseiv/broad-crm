"""Репозиторий привязок Telegram ↔ CRM-пользователь (modules/sms, ADR-030).

Порт донорского `TelegramLinkRepository` на CRM-модели (UUID `user_id`, M2M
`user_teams`). `upsert` атомарен (`ON CONFLICT (telegram_user_id) DO UPDATE`).
`recipients_for_team` = JOIN `user_teams` → `users` → `sms_telegram_links`
(`dead_at IS NULL`) — получатели fan-out команды.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sms_telegram_link import SmsTelegramLink
from app.models.team import user_teams
from app.models.user import User


@dataclass(frozen=True, slots=True)
class Recipient:
    """Получатель fan-out: CRM-пользователь + снимок chat_id живой привязки."""

    user_id: uuid.UUID
    telegram_user_id: int


class SmsTelegramLinkRepository:
    """Upsert/статус привязок + резолв получателей команды."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, *, telegram_user_id: int, user_id: uuid.UUID) -> SmsTelegramLink:
        """Идемпотентная привязка Telegram к CRM-юзеру (`ON CONFLICT DO UPDATE`).

        Перепривязка обновляет `user_id`, «оживляет» линк (`dead_at=NULL`) и бампит
        `created_at`. Привязывает **свой** Telegram (`user_id = principal.user_id`).
        """
        stmt = (
            pg_insert(SmsTelegramLink)
            .values(telegram_user_id=telegram_user_id, user_id=user_id)
            .on_conflict_do_update(
                index_elements=[SmsTelegramLink.telegram_user_id],
                set_={
                    "user_id": user_id,
                    "created_at": datetime.now(UTC),
                    "dead_at": None,
                },
            )
            .returning(SmsTelegramLink)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> SmsTelegramLink | None:
        """Привязка по иммутабельному `telegram_user_id` НЕЗАВИСИМО от `dead_at`.

        Для беспарольного SSO-резолва (ADR-031, id-first): линк ищется даже если
        помечен мёртвым — сервис его оживляет (`dead_at=NULL`) при активном юзере.
        """
        stmt = select(SmsTelegramLink).where(
            SmsTelegramLink.telegram_user_id == telegram_user_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_active_by_telegram_user_id(self, telegram_user_id: int) -> SmsTelegramLink | None:
        """Живая привязка (`dead_at IS NULL`) по chat_id или None."""
        stmt = select(SmsTelegramLink).where(
            SmsTelegramLink.telegram_user_id == telegram_user_id,
            SmsTelegramLink.dead_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def is_linked_active(self, telegram_user_id: int) -> bool:
        """True, если этот Telegram привязан к живому CRM-юзеру (для `auth`-статуса)."""
        return await self.get_active_by_telegram_user_id(telegram_user_id) is not None

    async def mark_dead(self, telegram_user_id: int) -> None:
        """Пометить привязку мёртвой (`403` от Bot API): `dead_at = now()`."""
        await self._session.execute(
            update(SmsTelegramLink)
            .where(
                SmsTelegramLink.telegram_user_id == telegram_user_id,
                SmsTelegramLink.dead_at.is_(None),
            )
            .values(dead_at=datetime.now(UTC))
        )

    async def recipients_for_team(self, team_id: uuid.UUID) -> list[Recipient]:
        """Получатели fan-out команды: участники `user_teams` с живой привязкой.

        JOIN `user_teams` → `users` → `sms_telegram_links` (`dead_at IS NULL`).
        Один пользователь с несколькими живыми привязками → несколько получателей.

        **Системная строка-якорь супер-админа исключена ЯВНО** (`NOT is_system`, ADR-051
        §1.4(в)): выборка читает `users` в обход `UserRepository`; неявно якорь отсекали
        бы лишь INNER JOIN'ы (у него нет строк ни в `user_teams`, ни в
        `sms_telegram_links`). Явный фильтр — defense-in-depth.
        """
        stmt = (
            select(User.id, SmsTelegramLink.telegram_user_id)
            .join(user_teams, user_teams.c.user_id == User.id)
            .join(SmsTelegramLink, SmsTelegramLink.user_id == User.id)
            .where(
                user_teams.c.team_id == team_id,
                User.is_system.is_(False),
                SmsTelegramLink.dead_at.is_(None),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [Recipient(user_id=uid, telegram_user_id=int(tg)) for uid, tg in rows]
