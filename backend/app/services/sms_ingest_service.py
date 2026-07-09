"""SMS-пайплайн приёма и fan-out (modules/sms#приём-sms-и-fan-out, ADR-030).

Порт донорского `application/services.py` на CRM-модели/сессии. `handle_incoming_sms`
— приём webhook: нормализация → дедуп по SID → сохранение → crash-recoverable
fan-out получателям команды. `retry_pending_deliveries` — переотправка pending/failed
(вызывается retry-монитором). Транзакционная модель донора сохранена
(`try_reserve` идемпотентен по UNIQUE `(inbound_sms_id, telegram_user_id)`).
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sms import normalize_phone
from app.infra.sms_telegram import (
    SmsBotClient,
    TelegramApiError,
    TelegramForbiddenError,
)
from app.logging import get_logger
from app.models.sms_inbound import SmsInbound
from app.repositories.sms_delivery_repository import SmsDeliveryRepository
from app.repositories.sms_inbound_repository import SmsInboundRepository
from app.repositories.sms_number_repository import SmsNumberRepository
from app.repositories.sms_telegram_link_repository import SmsTelegramLinkRepository

logger = get_logger(__name__)

# Лимит символов Telegram-сообщения перед разбиением на части.
_TELEGRAM_MESSAGE_LIMIT = 3500


def format_sms_message(sms: SmsInbound) -> str:
    """Текст уведомления оператору (нормативный формат, modules/sms#текст-сообщения).

    Время — локальное отправки (`received_at` уже tz-aware); формат `DD.MM HH:MM`.
    """
    local_time = sms.received_at.strftime("%d.%m %H:%M")
    return (
        "📩 Новое SMS\n\n"
        f"📱 Номер: {sms.to_number}\n"
        f"👤 От: {sms.from_number}\n"
        f"💬 Текст: {sms.body}\n"
        f"🕒 Время: {local_time}"
    )


def _split_message(text: str, limit: int = _TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Разбивает длинное сообщение (> limit) на части по строкам (порт донора)."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        if len(line) <= limit:
            current = line
            continue
        start = 0
        while start < len(line):
            parts.append(line[start : start + limit])
            start += limit
        current = ""
    if current:
        parts.append(current)
    return parts or [text[:limit]]


async def _send_text(bot: SmsBotClient, chat_id: int, text: str) -> None:
    for part in _split_message(text):
        await bot.send_message(chat_id, part)


class SmsIngestService:
    """Приём входящего SMS и crash-recoverable fan-out операторам команды."""

    def __init__(self, session: AsyncSession, bot: SmsBotClient) -> None:
        self._session = session
        self._bot = bot

    async def handle_incoming_sms(
        self,
        *,
        twilio_message_sid: str | None,
        from_number: str,
        to_number: str,
        body: str,
        raw_payload: dict[str, str],
    ) -> SmsInbound:
        """Сохранить входящее SMS и разослать получателям команды (порт донора).

        Дедуп по `twilio_message_sid` (partial-UNIQUE). Дубликат/webhook-retry НЕ
        делает ранний возврат — падает в общий fan-out (crash-recovery): `try_reserve`
        идемпотентен, поэтому retry Twilio добирает получателей, которым доставка не
        была создана до крэша. Гонка insert (`IntegrityError`) → чтение уже
        сохранённого SMS.
        """
        session = self._session
        normalized_to = normalize_phone(to_number)
        normalized_from = normalize_phone(from_number)

        numbers = SmsNumberRepository(session)
        inbound = SmsInboundRepository(session)

        sms: SmsInbound | None = None
        try:
            async with session.begin():
                if twilio_message_sid:
                    sms = await inbound.find_by_sid(twilio_message_sid)
                    if sms is not None:
                        logger.info("sms_duplicate_sid")
                if sms is None:
                    number = await numbers.find_by_phone(normalized_to)
                    team_id = number.team_id if number is not None else None
                    sms = await inbound.create(
                        twilio_message_sid=twilio_message_sid,
                        from_number=normalized_from,
                        to_number=normalized_to,
                        body=body,
                        team_id=team_id,
                        raw_payload=dict(raw_payload),
                    )
        except IntegrityError:
            # Конкурентный webhook с тем же MessageSid уронил insert на partial-UNIQUE.
            if not twilio_message_sid:
                raise
            async with session.begin():
                sms = await inbound.find_by_sid(twilio_message_sid)
            if sms is None:
                raise
            logger.info("sms_duplicate_sid_race")

        assert sms is not None
        sms_id = sms.id
        sms_team_id = sms.team_id

        if sms_team_id is None:
            logger.warning("sms_unknown_number", to_number=normalized_to)
            return sms

        recipients = await SmsTelegramLinkRepository(session).recipients_for_team(sms_team_id)
        if not recipients:
            logger.warning("sms_no_recipients", team_id=str(sms_team_id))
            return sms

        # Закрыть autobegun read-tx перед write-транзакциями fan-out.
        await session.commit()

        for recipient in recipients:
            await self._deliver(
                sms=sms,
                sms_id=sms_id,
                user_id=recipient.user_id,
                telegram_user_id=recipient.telegram_user_id,
            )
        return sms

    async def _deliver(
        self,
        *,
        sms: SmsInbound,
        sms_id: int,
        user_id: uuid.UUID,
        telegram_user_id: int,
    ) -> None:
        deliveries = SmsDeliveryRepository(self._session)
        async with self._session.begin():
            delivery_id = await deliveries.try_reserve(
                inbound_sms_id=sms_id,
                user_id=user_id,
                telegram_user_id=telegram_user_id,
            )
        if delivery_id is None:
            return  # уже доставлялось (идемпотентность)
        await self._send_one(
            sms=sms,
            delivery_id=delivery_id,
            telegram_user_id=telegram_user_id,
        )

    async def _send_one(
        self,
        *,
        sms: SmsInbound,
        delivery_id: int,
        telegram_user_id: int,
    ) -> None:
        """Отправить SMS одному получателю и зафиксировать статус доставки."""
        deliveries = SmsDeliveryRepository(self._session)
        links = SmsTelegramLinkRepository(self._session)

        if not self._bot.is_configured:
            async with self._session.begin():
                await deliveries.mark_failed(delivery_id, "SMS_TELEGRAM_BOT_TOKEN не настроен")
            return

        try:
            await _send_text(self._bot, telegram_user_id, format_sms_message(sms))
        except TelegramForbiddenError as exc:
            async with self._session.begin():
                await deliveries.mark_dead(delivery_id, str(exc))
                await links.mark_dead(telegram_user_id)
            logger.warning("sms_delivery_dead", telegram_user_id=telegram_user_id)
            return
        except TelegramApiError as exc:
            async with self._session.begin():
                await deliveries.mark_failed(delivery_id, str(exc))
            logger.warning("sms_delivery_failed", telegram_user_id=telegram_user_id)
            return

        async with self._session.begin():
            await deliveries.mark_sent(delivery_id)

    async def retry_pending_deliveries(self, max_attempts: int) -> int:
        """Переотправить pending/failed доставки (chat_id из снимка delivery).

        Отсутствует исходное SMS → `mark_failed`; линк мёртв → `mark_dead`; иначе —
        повтор отправки. Возвращает число обработанных (переотправленных) доставок.
        """
        session = self._session
        deliveries = SmsDeliveryRepository(session)
        inbound = SmsInboundRepository(session)
        links = SmsTelegramLinkRepository(session)

        pending = await deliveries.pending(max_attempts)
        await session.commit()  # закрыть autobegun read-tx

        retried = 0
        for delivery in pending:
            delivery_id = delivery.id
            inbound_sms_id = delivery.inbound_sms_id
            telegram_user_id = delivery.telegram_user_id

            sms = await inbound.get(inbound_sms_id)
            active_link = (
                await links.get_active_by_telegram_user_id(telegram_user_id)
                if sms is not None
                else None
            )
            await session.commit()  # закрыть autobegun read-tx перед session.begin()

            if sms is None:
                async with session.begin():
                    await deliveries.mark_failed(delivery_id, "Исходное SMS не найдено")
                continue
            if active_link is None:
                async with session.begin():
                    await deliveries.mark_dead(delivery_id, "Привязка Telegram недоступна")
                continue

            await self._send_one(
                sms=sms,
                delivery_id=delivery_id,
                telegram_user_id=telegram_user_id,
            )
            retried += 1
        return retried


__all__ = ["SmsIngestService", "format_sms_message"]
