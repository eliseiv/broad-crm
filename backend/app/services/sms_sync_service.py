"""Синхронизация входящих номеров Twilio (modules/sms, 04-api.md#post-apismsnumberssync).

Порт донорского `twilio_sync_service`. Тянет все входящие номера аккаунта
(пагинация в SDK), нормализует (E.164), идемпотентно upsert'ит как unassigned
(`ON CONFLICT (phone_number) DO NOTHING`) и обновляет `label` из Twilio
`friendly_name`. Синхронный Twilio SDK выносится в threadpool (`asyncio.to_thread`).
`twilio_not_configured` → 503; сбой Twilio API → 502 twilio_error.
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.domain.sms import normalize_phone
from app.errors import twilio_error, twilio_not_configured
from app.infra.twilio_numbers import (
    TwilioNotConfiguredError,
    TwilioNumber,
    TwilioNumbersApiError,
    TwilioNumbersClient,
)
from app.logging import get_logger
from app.repositories.sms_number_repository import SmsNumberRepository
from app.schemas.sms import SmsSyncResult

logger = get_logger(__name__)


class SmsSyncService:
    """On-demand синхронизация Twilio-номеров в `sms_phone_numbers` как unassigned."""

    def __init__(self, *, numbers: SmsNumberRepository, settings: Settings) -> None:
        self._numbers = numbers
        self._settings = settings

    async def sync(self) -> SmsSyncResult:
        """Синхронизирует номера Twilio-аккаунта. → 503 если не настроен; 502 при сбое API."""
        if not self._settings.twilio_configured:
            raise twilio_not_configured()

        client = TwilioNumbersClient(
            self._settings.twilio_account_sid, self._settings.twilio_auth_token
        )
        try:
            raw = await asyncio.to_thread(client.list_incoming_numbers)
        except TwilioNotConfiguredError as exc:
            raise twilio_not_configured() from exc
        except TwilioNumbersApiError as exc:
            logger.warning("sms_sync_twilio_error")
            raise twilio_error() from exc

        synced_total = len(raw)
        deduped = self._dedupe_normalize(raw)

        # 1. Вставка новых как unassigned (DO NOTHING) → число реально добавленных.
        added = await self._numbers.bulk_upsert_unassigned([phone for phone, _ in deduped])
        # 2. Обновление системного label из friendly_name для всех синхронизированных.
        for phone, friendly in deduped:
            await self._numbers.update_label(phone, friendly)
        await self._numbers.session.commit()

        result = SmsSyncResult(
            synced_total=synced_total,
            added=added,
            skipped_existing=synced_total - added,
        )
        logger.info(
            "sms_numbers_synced",
            synced_total=result.synced_total,
            added=result.added,
            skipped_existing=result.skipped_existing,
        )
        return result

    @staticmethod
    def _dedupe_normalize(
        raw: list[TwilioNumber],
    ) -> list[tuple[str, str | None]]:
        """Нормализует (E.164) и дедуплицирует по номеру, сохраняя первый friendly_name."""
        seen: set[str] = set()
        result: list[tuple[str, str | None]] = []
        for item in raw:
            normalized = normalize_phone(item.phone_number)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append((normalized, item.friendly_name))
        return result
