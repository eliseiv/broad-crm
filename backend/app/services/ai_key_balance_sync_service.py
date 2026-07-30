"""Фоновая синхронизация оценочного баланса AI-ключей (ADR-070)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.notifications import (
    BackendRef,
    build_key_balance_depleted,
    build_key_balance_low,
    build_key_balance_recovered,
    build_key_balance_sync_failed,
)
from app.infra.ai_provider_billing import (
    BalanceSyncResult,
    compute_alert_level,
    default_low_threshold_usd,
    sync_balance,
)
from app.infra.crypto import CryptoError, decrypt_secret
from app.infra.telegram import TelegramClient
from app.logging import get_logger
from app.models.ai_key import AiKey, AiProvider, BalanceAlertLevel, BalanceSyncStatus
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.backend_repository import BackendRepository
from app.services.alert_backend_refs import to_backend_refs

logger = get_logger(__name__)

_MAX_CONCURRENT_SYNC = 3
_SYNC_FAIL_ALERT_THRESHOLD = 3

BalanceAlert = Literal["low", "depleted", "recovered", "sync_failed"]


@dataclass(frozen=True)
class BalanceSnapshot:
    """Снимок ключа для balance-sync (сессия БД закрыта до HTTP)."""

    id: uuid.UUID
    name: str
    provider: str
    key_prefix: str | None
    key_last4: str | None
    balance_initial_usd: Decimal
    balance_anchor_at: datetime
    balance_low_threshold_usd: Decimal
    balance_alert_level: str | None
    balance_sync_fail_streak: int
    billing_admin_key_encrypted: bytes
    provider_api_key_id: str | None


def evaluate_balance_alert_transition(
    old_level: str | None,
    new_level: str,
    *,
    sync_failed_alert: bool,
) -> BalanceAlert | None:
    """Переходы уровня остатка и алерт при серии ошибок sync."""
    if sync_failed_alert:
        return "sync_failed"
    if old_level == new_level:
        return None
    if new_level == BalanceAlertLevel.depleted.value:
        return "depleted"
    if new_level == BalanceAlertLevel.low.value and old_level != BalanceAlertLevel.low.value:
        return "low"
    if new_level == BalanceAlertLevel.normal.value and old_level in (
        BalanceAlertLevel.low.value,
        BalanceAlertLevel.depleted.value,
    ):
        return "recovered"
    return None


class AiKeyBalanceSyncService:
    """Периодическая синхронизация оценочного остатка через Admin Cost API."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        telegram: TelegramClient | None,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._telegram = telegram
        self._interval_sec = settings.ai_key_balance_sync_interval_sec

    async def sync_one(self, ai_key_id: uuid.UUID) -> None:
        try:
            async with self._sessionmaker() as session:
                repo = AiKeyRepository(session)
                ai_key = await repo.get_by_id(ai_key_id)
                if ai_key is None or not ai_key.balance_monitoring_enabled:
                    return
                snapshot = _snapshot_from_row(ai_key)
                if snapshot is None:
                    return
            await self._sync_snapshot(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "ai_key_balance_sync_one_failed",
                ai_key_id=str(ai_key_id),
                error_type=type(exc).__name__,
            )

    async def poll_once(self) -> None:
        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
            keys = await repo.list_balance_monitored()
            snapshots = [s for key in keys if (s := _snapshot_from_row(key)) is not None]
        if not snapshots:
            return
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_SYNC)

        async def _guarded(snapshot: BalanceSnapshot) -> None:
            async with semaphore:
                await self._sync_snapshot(snapshot)

        await asyncio.gather(*(_guarded(snapshot) for snapshot in snapshots))

    async def _sync_snapshot(self, snapshot: BalanceSnapshot) -> None:
        try:
            admin_key = decrypt_secret(snapshot.billing_admin_key_encrypted)
        except CryptoError:
            logger.error("ai_key_billing_admin_decrypt_failed", ai_key_id=str(snapshot.id))
            return

        try:
            provider = AiProvider(snapshot.provider)
        except ValueError:
            logger.error("ai_key_balance_unknown_provider", ai_key_id=str(snapshot.id))
            return

        try:
            result = await sync_balance(
                provider,
                admin_key,
                key_prefix=snapshot.key_prefix,
                key_last4=snapshot.key_last4,
                balance_initial_usd=snapshot.balance_initial_usd,
                balance_anchor_at=snapshot.balance_anchor_at,
                cached_api_key_id=snapshot.provider_api_key_id,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            result = BalanceSyncResult("unknown")
        except httpx.HTTPError:
            result = BalanceSyncResult("unknown")
        if result.outcome == "unknown":
            await self._persist_unknown(snapshot)
            return

        if result.outcome == "error":
            await self._persist_error(snapshot, result.reason or "Ошибка billing API")
            return

        assert result.remaining_usd is not None
        threshold = snapshot.balance_low_threshold_usd
        new_level = compute_alert_level(result.remaining_usd, threshold)
        old_level = snapshot.balance_alert_level
        sync_failed_alert = False

        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
            await repo.update_balance_sync(
                snapshot.id,
                remaining_usd=result.remaining_usd,
                sync_status=BalanceSyncStatus.ok.value,
                sync_error=None,
                last_sync_at=datetime.now(UTC),
                alert_level=new_level,
                provider_api_key_id=result.provider_api_key_id,
                sync_fail_streak=0,
            )
            await session.commit()

        alert = evaluate_balance_alert_transition(
            old_level,
            new_level,
            sync_failed_alert=sync_failed_alert,
        )
        if alert is not None:
            await self._send_alert(alert, snapshot, result.remaining_usd, threshold, None)

    async def _persist_unknown(self, snapshot: BalanceSnapshot) -> None:
        streak = snapshot.balance_sync_fail_streak + 1
        sync_failed_alert = streak >= _SYNC_FAIL_ALERT_THRESHOLD
        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
            await repo.update_balance_sync(
                snapshot.id,
                remaining_usd=None,
                sync_status=BalanceSyncStatus.unknown.value,
                sync_error=None,
                last_sync_at=None,
                alert_level=snapshot.balance_alert_level,
                provider_api_key_id=snapshot.provider_api_key_id,
                sync_fail_streak=streak,
            )
            await session.commit()
        if sync_failed_alert:
            await self._send_alert(
                "sync_failed",
                snapshot,
                None,
                snapshot.balance_low_threshold_usd,
                "Провайдер временно недоступен",
            )

    async def _persist_error(self, snapshot: BalanceSnapshot, reason: str) -> None:
        streak = snapshot.balance_sync_fail_streak + 1
        sync_failed_alert = streak >= _SYNC_FAIL_ALERT_THRESHOLD
        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
            await repo.update_balance_sync(
                snapshot.id,
                remaining_usd=None,
                sync_status=BalanceSyncStatus.error.value,
                sync_error=reason,
                last_sync_at=datetime.now(UTC),
                alert_level=snapshot.balance_alert_level,
                provider_api_key_id=None,
                sync_fail_streak=streak,
            )
            await session.commit()
        if sync_failed_alert:
            await self._send_alert(
                "sync_failed",
                snapshot,
                None,
                snapshot.balance_low_threshold_usd,
                reason,
            )

    async def _send_alert(
        self,
        alert: BalanceAlert,
        snapshot: BalanceSnapshot,
        remaining_usd: Decimal | None,
        threshold_usd: Decimal,
        sync_reason: str | None,
    ) -> None:
        if self._telegram is None:
            logger.info("ai_key_balance_alert_suppressed_no_telegram", ai_key_id=str(snapshot.id))
            return
        backends: list[BackendRef] = ()
        if alert in ("low", "depleted", "sync_failed"):
            backends = await self._backend_refs(snapshot.id)
        if alert == "low" and remaining_usd is not None:
            text = build_key_balance_low(
                snapshot.name,
                snapshot.key_last4,
                remaining_usd,
                threshold_usd,
                backends,
            )
        elif alert == "depleted" and remaining_usd is not None:
            text = build_key_balance_depleted(
                snapshot.name,
                snapshot.key_last4,
                remaining_usd,
                backends,
            )
        elif alert == "recovered" and remaining_usd is not None:
            text = build_key_balance_recovered(snapshot.name, snapshot.key_last4, remaining_usd)
        else:
            text = build_key_balance_sync_failed(
                snapshot.name,
                snapshot.key_last4,
                sync_reason or "Ошибка billing API",
                backends,
            )
        await self._telegram.send_message(text)

    async def _backend_refs(self, ai_key_id: uuid.UUID) -> list[BackendRef]:
        async with self._sessionmaker() as session:
            backends = await BackendRepository(session).list_by_ai_key(ai_key_id)
            return to_backend_refs(backends)

    async def run(self) -> None:
        logger.info("ai_key_balance_sync_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as exc:
                    logger.error(
                        "ai_key_balance_sync_poll_failed",
                        error_type=type(exc).__name__,
                    )
                await asyncio.sleep(self._interval_sec)
        except asyncio.CancelledError:
            logger.info("ai_key_balance_sync_stopped")
            raise


def _snapshot_from_row(ai_key: AiKey) -> BalanceSnapshot | None:
    if not ai_key.balance_monitoring_enabled:
        return None
    if (
        ai_key.balance_initial_usd is None
        or ai_key.balance_anchor_at is None
        or ai_key.billing_admin_key_encrypted is None
    ):
        return None
    threshold = ai_key.balance_low_threshold_usd or default_low_threshold_usd()
    return BalanceSnapshot(
        id=ai_key.id,
        name=ai_key.name,
        provider=ai_key.provider,
        key_prefix=ai_key.key_prefix,
        key_last4=ai_key.key_last4,
        balance_initial_usd=ai_key.balance_initial_usd,
        balance_anchor_at=ai_key.balance_anchor_at,
        balance_low_threshold_usd=threshold,
        balance_alert_level=ai_key.balance_alert_level,
        balance_sync_fail_streak=ai_key.balance_sync_fail_streak,
        billing_admin_key_encrypted=ai_key.billing_admin_key_encrypted,
        provider_api_key_id=ai_key.provider_api_key_id,
    )


__all__ = [
    "AiKeyBalanceSyncService",
    "BalanceAlert",
    "BalanceSnapshot",
    "evaluate_balance_alert_transition",
]
