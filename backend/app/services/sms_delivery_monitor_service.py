"""Фоновый retry-монитор доставок SMS (modules/sms, ADR-030).

Отдельная asyncio-задача (по образцу `proxy_monitor_service`). Периодически
(`SMS_DELIVERY_RETRY_INTERVAL_SEC`) добирает `sms_deliveries` со `status ∈
(pending, failed)` и `attempts < SMS_DELIVERY_MAX_ATTEMPTS` (partial-индекс
`ix_sms_deliveries_retry`) и переотправляет их. Стартует в `lifespan` только при
`sms_bot_enabled` (задан `SMS_TELEGRAM_BOT_TOKEN`). Ошибка итерации логируется,
цикл живёт.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.infra.sms_telegram import SmsBotClient
from app.logging import get_logger
from app.services.sms_ingest_service import SmsIngestService

logger = get_logger(__name__)


class SmsDeliveryMonitorService:
    """Периодическая переотправка pending/failed доставок SMS в Telegram."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        bot: SmsBotClient,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._bot = bot
        self._interval_sec = settings.sms_delivery_retry_interval_sec
        self._max_attempts = settings.sms_delivery_max_attempts

    async def poll_once(self) -> int:
        """Одна итерация: переотправить кандидатов на retry. Возвращает их число."""
        async with self._sessionmaker() as session:
            service = SmsIngestService(session, self._bot)
            return await service.retry_pending_deliveries(self._max_attempts)

    async def run(self) -> None:
        """Бесконечный цикл: retry → sleep. Ошибка итерации логируется, цикл живёт."""
        logger.info("sms_delivery_monitor_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error(
                        "sms_delivery_monitor_poll_failed",
                        error_type=type(exc).__name__,
                    )
                await asyncio.sleep(self._interval_sec)
        except asyncio.CancelledError:
            logger.info("sms_delivery_monitor_stopped")
            raise


__all__ = ["SmsDeliveryMonitorService"]
