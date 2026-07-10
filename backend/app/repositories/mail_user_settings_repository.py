"""Репозиторий `mail_user_settings` — opt-out Telegram-уведомлений (ADR-044 §2, MAJOR-4).

Дефолт (нет строки) = уведомления включены. `upsert` — идемпотентная запись флага по
`principal.user_id`; `get` — чтение текущего значения (None → строки нет → включено).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mail_user_settings import MailUserSettings


class MailUserSettingsRepository:
    """Чтение/запись opt-out уведомлений пользователя."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> bool | None:
        """Текущее `tg_notifications_enabled` или None (строки нет → дефолт включено)."""
        stmt = select(MailUserSettings.tg_notifications_enabled).where(
            MailUserSettings.user_id == user_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert(self, *, user_id: uuid.UUID, enabled: bool) -> None:
        """Идемпотентно установить флаг opt-out (`ON CONFLICT (user_id) DO UPDATE`)."""
        stmt = (
            pg_insert(MailUserSettings)
            .values(user_id=user_id, tg_notifications_enabled=enabled)
            .on_conflict_do_update(
                index_elements=[MailUserSettings.user_id],
                set_={"tg_notifications_enabled": enabled, "updated_at": datetime.now(UTC)},
            )
        )
        await self._session.execute(stmt)
