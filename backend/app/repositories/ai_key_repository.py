"""Репозиторий реестра AI-ключей (SQLAlchemy 2.0 async)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

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
        balance_monitoring_enabled: bool = False,
        balance_initial_usd: Decimal | None = None,
        balance_remaining_usd: Decimal | None = None,
        balance_low_threshold_usd: Decimal | None = None,
        balance_anchor_at: datetime | None = None,
        balance_sync_status: str | None = None,
        balance_alert_level: str | None = None,
        billing_admin_key_encrypted: bytes | None = None,
    ) -> AiKey:
        """Создаёт ключ со статусом pending (check_status по умолчанию)."""
        ai_key = AiKey(
            name=name,
            provider=provider,
            key_encrypted=key_encrypted,
            key_prefix=key_prefix,
            key_last4=key_last4,
            check_status=AiKeyStatus.pending.value,
            balance_monitoring_enabled=balance_monitoring_enabled,
            balance_initial_usd=balance_initial_usd,
            balance_remaining_usd=balance_remaining_usd,
            balance_low_threshold_usd=balance_low_threshold_usd,
            balance_anchor_at=balance_anchor_at,
            balance_sync_status=balance_sync_status,
            balance_alert_level=balance_alert_level,
            billing_admin_key_encrypted=billing_admin_key_encrypted,
        )
        self._session.add(ai_key)
        await self._session.flush()
        await self._session.refresh(ai_key)
        return ai_key

    async def list_all(self) -> list[AiKey]:
        """Все ключи, сортировка `position ASC, created_at DESC, id` (04-api.md).

        Используется как для списка API, так и для снимка ключей монитором
        (для монитора порядок несуществен).
        """
        stmt = select(AiKey).order_by(AiKey.position.asc(), AiKey.created_at.desc(), AiKey.id.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, ai_key_id: uuid.UUID) -> AiKey | None:
        """Возвращает ключ по id или None."""
        return await self._session.get(AiKey, ai_key_id)

    async def all_ids(self) -> set[uuid.UUID]:
        """Множество id всех ключей (любой провайдер) — для проверки существования (404)."""
        result = await self._session.execute(select(AiKey.id))
        return set(result.scalars().all())

    async def ids_by_provider(self, provider: str) -> set[uuid.UUID]:
        """Множество id ключей одного провайдера — ожидаемая группа для reorder (422)."""
        result = await self._session.execute(select(AiKey.id).where(AiKey.provider == provider))
        return set(result.scalars().all())

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        """Присваивает `position = 0..M-1` по индексу в массиве (одна транзакция).

        Вызывается только после валидации, что `ordered_ids` — полная перестановка
        группы провайдера; коммит выполняет вызывающий сервис.
        """
        for index, ai_key_id in enumerate(ordered_ids):
            await self._session.execute(
                update(AiKey).where(AiKey.id == ai_key_id).values(position=index)
            )

    async def delete_by_id(self, ai_key_id: uuid.UUID) -> bool:
        """Hard-delete по id. True, если запись была удалена."""
        stmt = delete(AiKey).where(AiKey.id == ai_key_id)
        result = await self._session.execute(stmt)
        # CursorResult.rowcount не типизирован в SQLAlchemy stubs (известное ограничение).
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

    async def list_balance_monitored(self) -> list[AiKey]:
        """Ключи с включённым мониторингом баланса."""
        stmt = (
            select(AiKey)
            .where(AiKey.balance_monitoring_enabled.is_(True))
            .order_by(AiKey.position.asc(), AiKey.created_at.desc(), AiKey.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_balance_sync(
        self,
        ai_key_id: uuid.UUID,
        *,
        remaining_usd: Decimal | None,
        sync_status: str,
        sync_error: str | None,
        last_sync_at: datetime | None,
        alert_level: str | None,
        provider_api_key_id: str | None,
        sync_fail_streak: int,
    ) -> None:
        """Обновляет поля оценочного остатка после sync (ADR-070)."""
        values: dict[str, object] = {
            "balance_sync_status": sync_status,
            "balance_sync_error": sync_error,
            "balance_last_sync_at": last_sync_at,
            "balance_alert_level": alert_level,
            "balance_sync_fail_streak": sync_fail_streak,
            "updated_at": func.now(),
        }
        if remaining_usd is not None:
            values["balance_remaining_usd"] = remaining_usd
        if provider_api_key_id is not None:
            values["provider_api_key_id"] = provider_api_key_id
        stmt = update(AiKey).where(AiKey.id == ai_key_id).values(**values)
        await self._session.execute(stmt)

    async def update_credit_probe(
        self,
        ai_key_id: uuid.UUID,
        *,
        credit_status: str,
        credit_probe_error: str | None,
        credit_last_probed_at: datetime,
    ) -> None:
        """Обновляет поля credit-probe (ADR-075)."""
        stmt = (
            update(AiKey)
            .where(AiKey.id == ai_key_id)
            .values(
                credit_status=credit_status,
                credit_probe_error=credit_probe_error,
                credit_last_probed_at=credit_last_probed_at,
                updated_at=func.now(),
            )
        )
        await self._session.execute(stmt)
