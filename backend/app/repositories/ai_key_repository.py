"""Репозиторий реестра AI-ключей (SQLAlchemy 2.0 async)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_key import AiKey, AiKeyStatus


class AiKeyRepository:
    """CRUD-операции над таблицей `ai_keys` + обновление статуса проверки."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (для управления транзакцией в сервисе)."""
        return self._session

    async def create(
        self,
        *,
        name: str,
        provider: str,
        key_encrypted: bytes,
        key_prefix: str | None,
        key_last4: str | None,
    ) -> AiKey:
        """Создаёт ключ со статусом pending (check_status по умолчанию)."""
        ai_key = AiKey(
            name=name,
            provider=provider,
            key_encrypted=key_encrypted,
            key_prefix=key_prefix,
            key_last4=key_last4,
            check_status=AiKeyStatus.pending.value,
        )
        self._session.add(ai_key)
        await self._session.flush()
        await self._session.refresh(ai_key)
        return ai_key

    async def list_all(self) -> list[AiKey]:
        """Все ключи, сортировка created_at DESC, вторичный ключ id (04-api.md).

        Используется как для списка API, так и для снимка ключей монитором.
        """
        stmt = select(AiKey).order_by(AiKey.created_at.desc(), AiKey.id.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, ai_key_id: uuid.UUID) -> AiKey | None:
        """Возвращает ключ по id или None."""
        return await self._session.get(AiKey, ai_key_id)

    async def delete_by_id(self, ai_key_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(AiKey).where(AiKey.id == ai_key_id)
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0  # type: ignore[attr-defined]

    async def update_check(
        self,
        ai_key_id: uuid.UUID,
        *,
        status: str,
        error_message: str | None,
        last_checked_at: datetime,
    ) -> None:
        """Атомарно обновляет результат проверки (check_status, error_message,
        last_checked_at, updated_at) одним UPDATE (modules/ai-keys)."""
        stmt = (
            update(AiKey)
            .where(AiKey.id == ai_key_id)
            .values(
                check_status=status,
                error_message=error_message,
                last_checked_at=last_checked_at,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)
