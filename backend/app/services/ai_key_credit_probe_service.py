"""Фоновый hourly credit-probe AI-ключей (ADR-075).

Минимальный платный inference без Admin API key. Состояние `credit_status`
персистентно в БД (антиспам Telegram). Probe только для `check_status=working`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.notifications import (
    BackendRef,
    build_key_credit_depleted,
    build_key_credit_recovered,
)
from app.infra.ai_provider import CreditProbeResult, probe_credits
from app.infra.crypto import CryptoError, decrypt_secret
from app.infra.telegram import TelegramClient
from app.logging import get_logger
from app.models.ai_key import AiKeyStatus, AiProvider, CreditStatus
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.backend_repository import BackendRepository
from app.services.alert_backend_refs import to_backend_refs

logger = get_logger(__name__)

_MAX_CONCURRENT = 3

Alert = Literal["depleted", "recovered"]


@dataclass(frozen=True)
class CreditSnapshot:
    id: uuid.UUID
    name: str
    provider: str
    key_encrypted: bytes
    key_last4: str | None
    prev_credit_status: str | None
    check_status: str


def evaluate_credit_transition(
    old_status: str | None, result: CreditProbeResult
) -> tuple[str | None, str | None, Alert | None]:
    """Чистая функция перехода credit_status (ADR-075).

    `unknown` → не меняем. `ok`/`depleted` → пишем; алерт только на смене
    границы depleted↔ok (и первое обнаружение depleted).
    """
    if result.outcome == "unknown":
        return old_status, None, None

    if result.outcome == "ok":
        alert: Alert | None = "recovered" if old_status == CreditStatus.depleted.value else None
        return CreditStatus.ok.value, None, alert

    # depleted
    alert = "depleted" if old_status != CreditStatus.depleted.value else None
    return CreditStatus.depleted.value, result.reason, alert


class AiKeyCreditProbeService:
    """Периодический credit-probe всех working-ключей."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        telegram: TelegramClient | None,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._telegram = telegram
        self._interval_sec = settings.ai_key_credit_probe_interval_sec

    async def poll_once(self) -> None:
        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
            keys = await repo.list_all()
            snapshots = [
                CreditSnapshot(
                    id=key.id,
                    name=key.name,
                    provider=key.provider,
                    key_encrypted=key.key_encrypted,
                    key_last4=key.key_last4,
                    prev_credit_status=key.credit_status,
                    check_status=key.check_status,
                )
                for key in keys
                if key.check_status == AiKeyStatus.working.value
            ]

        if not snapshots:
            return

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT)

        async def _guarded(snapshot: CreditSnapshot) -> None:
            async with semaphore:
                await self._probe_snapshot(snapshot)

        await asyncio.gather(*(_guarded(s) for s in snapshots))

    async def _probe_snapshot(self, snapshot: CreditSnapshot) -> None:
        try:
            api_key = decrypt_secret(snapshot.key_encrypted)
        except CryptoError:
            logger.error("ai_key_credit_decrypt_failed", ai_key_id=str(snapshot.id))
            return

        try:
            provider = AiProvider(snapshot.provider)
        except ValueError:
            logger.error("ai_key_credit_unknown_provider", ai_key_id=str(snapshot.id))
            return

        result = await probe_credits(provider, api_key)
        new_status, error_message, alert = evaluate_credit_transition(
            snapshot.prev_credit_status, result
        )

        if result.outcome == "unknown":
            logger.warning("ai_key_credit_probe_unknown", ai_key_id=str(snapshot.id))
            return

        now = datetime.now(UTC)
        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
            await repo.update_credit_probe(
                snapshot.id,
                credit_status=new_status or CreditStatus.ok.value,
                credit_probe_error=error_message,
                credit_last_probed_at=now,
            )
            await session.commit()

        if alert is not None:
            await self._send_alert(alert, snapshot)

    async def _send_alert(self, alert: Alert, snapshot: CreditSnapshot) -> None:
        if self._telegram is None:
            logger.info("ai_key_credit_alert_suppressed_no_telegram", ai_key_id=str(snapshot.id))
            return
        if alert == "depleted":
            backends = await self._backend_refs(snapshot.id)
            text = build_key_credit_depleted(snapshot.name, snapshot.key_last4, backends)
        else:
            text = build_key_credit_recovered(snapshot.name, snapshot.key_last4)
        await self._telegram.send_message(text)

    async def _backend_refs(self, ai_key_id: uuid.UUID) -> list[BackendRef]:
        async with self._sessionmaker() as session:
            backends = await BackendRepository(session).list_by_ai_key(ai_key_id)
            return to_backend_refs(backends)

    async def run(self) -> None:
        logger.info("ai_key_credit_probe_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error("ai_key_credit_probe_poll_failed", error_type=type(exc).__name__)
                await asyncio.sleep(self._interval_sec)
        finally:
            logger.info("ai_key_credit_probe_stopped")


__all__ = [
    "AiKeyCreditProbeService",
    "CreditSnapshot",
    "evaluate_credit_transition",
]
