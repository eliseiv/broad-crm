"""Сервис Telegram-привязки оператора (modules/sms, 04-api.md, ADR-030 §3).

Под JWT (без Redis/pending). `link` — привязка **своего** Telegram к своему
CRM-юзеру (`principal.user_id`); `auth` — публичный статус-запрос привязки. Обе
проверяют `init_data` (HMAC-SHA256 + TTL, чистая функция `verify_init_data`).
Плохой HMAC → 401 invalid_init_data; протухший `auth_date` → 401 init_data_expired.
`init_data` (содержит подпись/PII) не логируется.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.sms import ValidatedInitData, verify_init_data
from app.errors import forbidden, init_data_expired, invalid_init_data
from app.logging import get_logger
from app.repositories.sms_telegram_link_repository import SmsTelegramLinkRepository
from app.schemas.sms import TelegramAuthResponse, TelegramLinkResponse

logger = get_logger(__name__)

# TTL initData (`auth_date`): 24 часа — защита от повторного использования старого
# initData. Значение времени инъектируется в чистую функцию для тестируемости.
_INIT_DATA_MAX_AGE_SEC = 24 * 3600


class SmsTelegramLinkService:
    """Привязка/статус Telegram-аккаунта оператора (Mini App под JWT / публичный auth)."""

    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def _verify(self, init_data: str) -> ValidatedInitData:
        """Проверяет initData; ошибка → 401 invalid_init_data / init_data_expired."""
        result = verify_init_data(
            init_data,
            bot_token=self._settings.sms_telegram_bot_token,
            max_age_seconds=_INIT_DATA_MAX_AGE_SEC,
        )
        if isinstance(result, str):
            if result == "expired":
                raise init_data_expired()
            raise invalid_init_data()
        return result

    async def link(self, *, user_id: uuid.UUID | None, init_data: str) -> TelegramLinkResponse:
        """Привязка своего Telegram к своему CRM-юзеру (идемпотентный upsert).

        Супер-админ без `uid` привязать линк не может → 403 forbidden (ADR-030 §7).
        """
        if user_id is None:
            raise forbidden()
        validated = self._verify(init_data)

        links = SmsTelegramLinkRepository(self._session)
        await links.upsert(telegram_user_id=validated.telegram_user_id, user_id=user_id)
        await self._session.commit()
        logger.info("sms_telegram_linked", user_id=str(user_id))
        return TelegramLinkResponse(linked=True, telegram_user_id=validated.telegram_user_id)

    async def auth(self, init_data: str) -> TelegramAuthResponse:
        """Публичный Mini App bootstrap: статус привязки текущего Telegram (без сессии)."""
        validated = self._verify(init_data)
        links = SmsTelegramLinkRepository(self._session)
        linked = await links.is_linked_active(validated.telegram_user_id)
        return TelegramAuthResponse(linked=linked, telegram_user_id=validated.telegram_user_id)
