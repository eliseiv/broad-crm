"""Сервис Telegram-привязки/SSO оператора (modules/sms, 04-api.md, ADR-030 §3, ADR-031).

Без Redis/pending. `link` — привязка **своего** Telegram к своему CRM-юзеру
(`principal.user_id`, под JWT); `auth` — публичный **беспарольный Telegram-SSO**
(ADR-031): резолв оператора по Telegram-идентичности → авто-upsert/revive линка →
выдача CRM access-JWT. Обе проверяют `init_data` (HMAC-SHA256 + TTL, чистая функция
`verify_init_data`). Плохой HMAC/структура → 401 invalid_init_data; протухший
`auth_date` → 401 init_data_expired; пустой `init_data` → 400 validation_error.
`init_data` (подпись/PII) и извлечённый `username` (PII) не логируются.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.domain.sms import ValidatedInitData, verify_init_data
from app.domain.telegram import normalize_telegram
from app.errors import (
    forbidden,
    init_data_expired,
    invalid_init_data,
    sms_operator_not_provisioned,
    validation_error,
)
from app.infra.jwt import issue_access_token
from app.logging import get_logger
from app.models.user import User
from app.repositories.sms_telegram_link_repository import SmsTelegramLinkRepository
from app.repositories.user_repository import UserRepository
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
        """Проверяет initData.

        Пустой `init_data` → 400 validation_error; плохой HMAC/структура → 401
        invalid_init_data; протухший `auth_date` → 401 init_data_expired.
        """
        if not init_data or not init_data.strip():
            raise validation_error("Пустые данные Telegram")
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
        """Беспарольный Telegram-SSO Mini App (ADR-031, §Резолв оператора).

        1. Валидация `init_data` → `telegram_user_id` + `username` (может быть None).
        2. id-first: линк по `telegram_user_id` (независимо от `dead_at`) → `user_id`;
           `dead_at` не пуст → revive.
        3. Bootstrap (только если линка нет): `username` → `normalize_telegram` →
           активный `users.telegram` → upsert линка.
        4. Юзер активен → идемпотентный `first_login_at` (ADR-028) → выпуск access-JWT
           (`sub`=`users.username`, `uid`/`role`/`superadmin:false`) → 200.
        5. Иначе → 403 sms_operator_not_provisioned.
        """
        validated = self._verify(init_data)
        telegram_user_id = validated.telegram_user_id

        links = SmsTelegramLinkRepository(self._session)
        users = UserRepository(self._session)

        resolved: User | None = None
        link = await links.get_by_telegram_user_id(telegram_user_id)
        if link is not None:
            # id-first: иммутабельный telegram_user_id первичен (переживает смену ника).
            candidate = await users.get_by_id(link.user_id)
            if candidate is not None and candidate.is_active:
                resolved = candidate
                if link.dead_at is not None:
                    link.dead_at = None  # revive привязки
        elif validated.username:
            # Bootstrap первого контакта по username (только если линка ещё нет).
            candidate = await users.get_by_telegram(normalize_telegram(validated.username))
            if candidate is not None and candidate.is_active:
                resolved = candidate
                await links.upsert(telegram_user_id=telegram_user_id, user_id=candidate.id)

        if resolved is None:
            raise sms_operator_not_provisioned()

        # Первый успешный вход: метка проставляется идемпотентно (ADR-028).
        if resolved.first_login_at is None:
            resolved.first_login_at = datetime.now(UTC)
        await self._session.commit()

        token, expires_in = issue_access_token(
            sub=resolved.username,
            role=resolved.role.name,
            superadmin=False,
            uid=str(resolved.id),
        )
        logger.info("sms_telegram_sso", user_id=str(resolved.id))
        return TelegramAuthResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
            telegram_user_id=telegram_user_id,
            linked=True,
        )
