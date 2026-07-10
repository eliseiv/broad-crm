"""Репозиторий привязок Telegram ↔ CRM-пользователь для почты (ADR-044 §2/§6).

`mail_telegram_links`: `telegram_user_id` (= chat_id) — ключ доставки; `user_id`
**NULLABLE** (orphan-линк без CRM-пользователя, ленивый резолв §6); `username` —
нормализованный lower-case Telegram-username (ключ первичного связывания). Методы:
`bind`/`upsert_orphan` (самопривязка `/start`, Mini App SSO), `mark_dead`,
`bind_orphans_for_user` (синхронный хук user-сервиса), `reconcile_orphans` (safety-net
диспетчера), плюс резолв получателей fan-out (участники команды + admin-уровень с
живым линком, минус opt-out).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_telegram import MailTelegramLink
from app.models.mail_user_settings import MailUserSettings
from app.models.role import Role
from app.models.team import user_teams
from app.models.user import User


@dataclass(frozen=True, slots=True)
class MailRecipient:
    """Получатель fan-out: CRM-пользователь + снимок chat_id живой привязки."""

    user_id: uuid.UUID
    telegram_user_id: int


@dataclass(frozen=True, slots=True)
class SeesAllCandidate:
    """Кандидат admin-уровня: получатель + права роли (для проверки sees-all)."""

    user_id: uuid.UUID
    telegram_user_id: int
    permissions: dict[str, list[str]]


class MailTelegramLinkRepository:
    """Привязки Telegram почты: upsert, orphan-резолв, резолв получателей."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> MailTelegramLink | None:
        """Привязка по иммутабельному `telegram_user_id` (вкл. dead/orphan) или None."""
        stmt = select(MailTelegramLink).where(MailTelegramLink.telegram_user_id == telegram_user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def bind(
        self, *, telegram_user_id: int, user_id: uuid.UUID, username: str | None
    ) -> MailTelegramLink:
        """Идемпотентная привязка chat_id к CRM-юзеру (upsert, revive `dead_at=NULL`).

        Используется при резолве пользователя (Mini App SSO / `/start`). Обновляет
        `user_id`, `username` и «оживляет» линк.
        """
        stmt = (
            pg_insert(MailTelegramLink)
            .values(telegram_user_id=telegram_user_id, user_id=user_id, username=username)
            .on_conflict_do_update(
                index_elements=[MailTelegramLink.telegram_user_id],
                set_={"user_id": user_id, "username": username, "dead_at": None},
            )
            .returning(MailTelegramLink)
        )
        return (await self._session.execute(stmt)).scalar_one()

    async def upsert_orphan(self, *, telegram_user_id: int, username: str | None) -> None:
        """Сохранить orphan-линк (`user_id` не резолвится): insert или update username.

        Существующий `user_id` НЕ затирается (обновляется только `username` для
        последующего ленивого резолва). Самопривязка `/start` от несопоставленного ника.
        """
        stmt = (
            pg_insert(MailTelegramLink)
            .values(telegram_user_id=telegram_user_id, user_id=None, username=username)
            .on_conflict_do_update(
                index_elements=[MailTelegramLink.telegram_user_id],
                set_={"username": username},
            )
        )
        await self._session.execute(stmt)

    async def mark_dead(self, telegram_user_id: int) -> None:
        """Пометить привязку мёртвой (`403` от Bot API): `dead_at = now()`."""
        await self._session.execute(
            update(MailTelegramLink)
            .where(
                MailTelegramLink.telegram_user_id == telegram_user_id,
                MailTelegramLink.dead_at.is_(None),
            )
            .values(dead_at=datetime.now(UTC))
        )

    async def bind_orphans_for_user(self, *, user_id: uuid.UUID, username: str) -> int:
        """Синхронный хук user-сервиса (§6): связать orphan-линки с этим username.

        `UPDATE ... SET user_id WHERE user_id IS NULL AND username = :username`.
        Возвращает число связанных линков. Вызывается при создании/правке пользователя
        (или смене `users.telegram`); `username` — нормализованный lower-case.
        """
        result = await self._session.execute(
            update(MailTelegramLink)
            .where(
                MailTelegramLink.user_id.is_(None),
                MailTelegramLink.username == username,
            )
            .values(user_id=user_id)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def reconcile_orphans(self) -> int:
        """Safety-net диспетчера (§6): связать orphan'ов с появившимися пользователями.

        `UPDATE l SET user_id=u.id FROM users u WHERE l.user_id IS NULL AND
        u.telegram IS NOT NULL AND lower(u.telegram) = l.username`. Дешёвый partial-скан
        по orphan-индексу. Возвращает число связанных.
        """
        result = await self._session.execute(
            text(
                """
                UPDATE mail_telegram_links l
                SET    user_id = u.id
                FROM   users u
                WHERE  l.user_id IS NULL
                  AND  u.telegram IS NOT NULL
                  AND  lower(u.telegram) = l.username
                """
            )
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def team_recipients(self, team_id: uuid.UUID) -> list[MailRecipient]:
        """Получатели команды: активные участники `user_teams` с живой привязкой.

        JOIN `user_teams` → `users` (`is_active`) → `mail_telegram_links` (`dead_at IS
        NULL`), LEFT JOIN `mail_user_settings` (opt-out `tg_notifications_enabled=false`
        → исключить). Один пользователь с несколькими живыми привязками → несколько строк.
        """
        stmt = (
            select(User.id, MailTelegramLink.telegram_user_id)
            .join(user_teams, user_teams.c.user_id == User.id)
            .join(MailTelegramLink, MailTelegramLink.user_id == User.id)
            .outerjoin(MailUserSettings, MailUserSettings.user_id == User.id)
            .where(
                user_teams.c.team_id == team_id,
                User.is_active.is_(True),
                MailTelegramLink.dead_at.is_(None),
                func.coalesce(MailUserSettings.tg_notifications_enabled, True).is_(True),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [MailRecipient(user_id=uid, telegram_user_id=int(tg)) for uid, tg in rows]

    async def sees_all_candidates(self) -> list[SeesAllCandidate]:
        """Кандидаты admin-уровня: все живые привязки активных юзеров + права роли.

        Сервис фильтрует по предикату «полный каталог прав» (§6 «super_admin с живым
        линком»). Opt-out исключён здесь же. `.env`-супер-админа тут нет (он не строка
        `users`).
        """
        stmt = (
            select(User.id, MailTelegramLink.telegram_user_id, Role.permissions)
            .join(MailTelegramLink, MailTelegramLink.user_id == User.id)
            .join(Role, Role.id == User.role_id)
            .outerjoin(MailUserSettings, MailUserSettings.user_id == User.id)
            .where(
                User.is_active.is_(True),
                MailTelegramLink.dead_at.is_(None),
                func.coalesce(MailUserSettings.tg_notifications_enabled, True).is_(True),
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            SeesAllCandidate(user_id=uid, telegram_user_id=int(tg), permissions=dict(perms))
            for uid, tg, perms in rows
        ]

    async def is_team_member(self, *, user_id: uuid.UUID, team_id: uuid.UUID) -> bool:
        """True, если пользователь состоит в команде (для visibility callback)."""
        stmt = select(user_teams.c.user_id).where(
            user_teams.c.user_id == user_id,
            user_teams.c.team_id == team_id,
        )
        return (await self._session.execute(stmt.limit(1))).first() is not None
