"""Сервис Telegram-слоя почты (ADR-044 §6/§7): Mini App SSO, `/start`, callback, opt-out.

- `auth` — беспарольный вход Mini App `/tg/mail` (HMAC `initData` — граница безопасности):
  резолв по `telegram_user_id`, иначе по `username → users.telegram` (ci) → авто-upsert
  линка → CRM access-JWT; не сопоставлен → 403 mail_operator_not_provisioned.
- `handle_start` — самопривязка `/start` (сохранить chat_id + username; orphan при
  несопоставленном нике) + приветствие с кнопкой Mini App.
- `handle_callback` — «Посмотреть сообщение» основного бота: резолв линка → visibility
  (участник команды ящика ИЛИ admin-уровень) → отправка полного тела.
- `handle_push_callback` — то же для push-бота: авторизация по MAIL_ADMIN_TELEGRAM_IDS +
  team-match (защита от подделки `mail:{id}` чужой команды).
- `get_settings`/`update_settings` — opt-out уведомлений.

`initData`/PII (username) не логируются. Резолв по username — регистронезависимо
(`normalize_telegram`): `@Katetown` (Telegram) ↔ `katetown` (`users.telegram`).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MailPushBot, Settings
from app.domain.mail_notify import format_message_body, split_for_telegram
from app.domain.permissions import full_catalog_permissions, permissions_subset
from app.domain.sms import ValidatedInitData, verify_init_data
from app.domain.telegram import normalize_telegram
from app.errors import (
    init_data_expired,
    invalid_init_data,
    mail_operator_not_provisioned,
    validation_error,
)
from app.infra.jwt import issue_access_token
from app.infra.mail_telegram import (
    MailBotClient,
    MailTelegramApiError,
    webapp_markup,
)
from app.logging import get_logger
from app.models.user import User
from app.repositories.mail_dispatch_repository import MailDispatchRepository
from app.repositories.mail_telegram_link_repository import MailTelegramLinkRepository
from app.repositories.mail_user_settings_repository import MailUserSettingsRepository
from app.repositories.user_repository import UserRepository
from app.schemas.mail_telegram import MailTelegramAuthResponse, MailUserSettingsResponse

logger = get_logger(__name__)

_WELCOME_TEXT = "Добро пожаловать! Откройте почту по кнопке ниже."
# Контракт callback_data почты: `mail:<положительное-число>` (ADR-044 §6).
_CALLBACK_PATTERN = re.compile(r"^mail:(\d+)$")


class MailTelegramService:
    """Mini App SSO / самопривязка / callback / opt-out почтовых Telegram-ботов."""

    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def _main_bot(self) -> MailBotClient:
        return MailBotClient(self._settings.mail_bot_token, self._settings.mail_bot_proxy_url)

    def _verify(self, init_data: str) -> ValidatedInitData:
        """Проверяет initData основным ботом (граница безопасности, ADR-044 §7).

        Пустой → 400 validation_error; плохой HMAC/структура → 401 invalid_init_data;
        протухший → 401 init_data_expired.
        """
        if not init_data or not init_data.strip():
            raise validation_error("Пустые данные Telegram")
        result = verify_init_data(
            init_data,
            bot_token=self._settings.mail_bot_token,
            max_age_seconds=self._settings.mail_tg_initdata_ttl_sec,
        )
        if isinstance(result, str):
            if result == "expired":
                raise init_data_expired()
            raise invalid_init_data()
        return result

    async def _resolve_user(self, *, telegram_user_id: int, username: str | None) -> User | None:
        """Резолв активного CRM-пользователя: id-first (линк), затем username (ci).

        При резолве по username — авто-upsert линка (bootstrap/ленивое связывание).
        Возвращает пользователя или None (не сопоставлен).
        """
        links = MailTelegramLinkRepository(self._session)
        users = UserRepository(self._session)
        username_norm = normalize_telegram(username) if username else None

        link = await links.get_by_telegram_user_id(telegram_user_id)
        if link is not None and link.user_id is not None:
            candidate = await users.get_by_id(link.user_id)
            if candidate is not None and candidate.is_active:
                if link.dead_at is not None:
                    await links.bind(
                        telegram_user_id=telegram_user_id,
                        user_id=candidate.id,
                        username=username_norm or link.username,
                    )
                return candidate

        if username_norm:
            candidate = await users.get_by_telegram(username_norm)
            if candidate is not None and candidate.is_active:
                await links.bind(
                    telegram_user_id=telegram_user_id,
                    user_id=candidate.id,
                    username=username_norm,
                )
                return candidate

        return None

    async def auth(self, init_data: str) -> MailTelegramAuthResponse:
        """Беспарольный Telegram-SSO Mini App `/tg/mail` (ADR-044 §7).

        Валидная подпись → резолв пользователя → выпуск CRM access-JWT; не сопоставлен →
        403 mail_operator_not_provisioned; пустой/битый/протухший initData → 400/401.
        """
        validated = self._verify(init_data)
        resolved = await self._resolve_user(
            telegram_user_id=validated.telegram_user_id, username=validated.username
        )
        if resolved is None:
            await self._session.rollback()
            raise mail_operator_not_provisioned()

        if resolved.first_login_at is None:
            resolved.first_login_at = datetime.now(UTC)
        await self._session.commit()

        token, expires_in = issue_access_token(
            sub=resolved.username,
            role=resolved.role.name,
            superadmin=False,
            uid=str(resolved.id),
        )
        logger.info("mail_telegram_sso", user_id=str(resolved.id))
        return MailTelegramAuthResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
            telegram_user_id=validated.telegram_user_id,
            linked=True,
        )

    async def handle_start(self, *, telegram_user_id: int, username: str | None) -> None:
        """Самопривязка `/start` (ADR-044 §6): резолв или orphan-линк + приветствие.

        Резолв успешен → bind (chat_id+user_id+username); не сопоставлен → orphan-линк
        (chat_id+username, user_id=NULL) для ленивого резолва. Затем приветствие с
        кнопкой Mini App (best-effort). Ошибки отправки не роняют обработчик.
        """
        links = MailTelegramLinkRepository(self._session)
        username_norm = normalize_telegram(username) if username else None
        resolved = await self._resolve_user(telegram_user_id=telegram_user_id, username=username)
        if resolved is None:
            await links.upsert_orphan(telegram_user_id=telegram_user_id, username=username_norm)
        await self._session.commit()
        logger.info("mail_telegram_start", linked=resolved is not None)

        bot = self._main_bot()
        if not bot.is_configured:
            return
        webapp_url = self._settings.mail_bot_webapp_url
        reply_markup = webapp_markup(webapp_url) if webapp_url else None
        try:
            await bot.send_message(telegram_user_id, _WELCOME_TEXT, reply_markup=reply_markup)
        except MailTelegramApiError:
            logger.warning("mail_telegram_start_send_failed")

    async def _send_message_body(
        self,
        *,
        bot: MailBotClient,
        chat_id: int,
        callback_query_id: str,
        message_id: int,
    ) -> None:
        """Загрузить письмо и отправить полное тело в чат (callback), затем снять часики."""
        dispatch = MailDispatchRepository(self._session)
        message = await dispatch.load_dispatch_message(message_id)
        if message is None:
            await bot.answer_callback_query(
                callback_query_id, text="Сообщение больше не доступно.", show_alert=True
            )
            return

        from_label = message.from_name or message.from_addr
        body = format_message_body(
            subject=message.subject,
            from_label=from_label,
            body_text=message.body_text,
            body_html=message.body_html,
        )
        try:
            for chunk in split_for_telegram(body):
                await bot.send_message(chat_id, chunk, parse_mode="HTML")
        except MailTelegramApiError:
            logger.warning("mail_callback_send_failed", message_id=message_id)
            await bot.answer_callback_query(
                callback_query_id, text="Не удалось отправить сообщение.", show_alert=True
            )
            return
        await bot.answer_callback_query(callback_query_id)

    async def handle_callback(
        self,
        *,
        callback_query_id: str,
        telegram_user_id: int,
        chat_id: int,
        data: str,
    ) -> None:
        """Callback основного бота «Посмотреть сообщение» (ADR-044 §6).

        Валидирует `mail:{id}`; резолвит живой линк → CRM-юзер; проверяет visibility
        (участник команды ящика ИЛИ admin-уровень); отправляет полное тело. Никогда не
        бросает (webhook отвечает 200).
        """
        bot = self._main_bot()
        match = _CALLBACK_PATTERN.match(data or "")
        if match is None:
            await bot.answer_callback_query(callback_query_id, text="Неподдерживаемое действие.")
            return
        message_id = int(match.group(1))

        links = MailTelegramLinkRepository(self._session)
        users = UserRepository(self._session)
        link = await links.get_by_telegram_user_id(telegram_user_id)
        if link is None or link.dead_at is not None or link.user_id is None:
            await bot.answer_callback_query(
                callback_query_id, text="Сессия истекла, откройте бот заново.", show_alert=True
            )
            return
        user = await users.get_by_id(link.user_id)
        if user is None or not user.is_active:
            await bot.answer_callback_query(
                callback_query_id, text="Сессия истекла, откройте бот заново.", show_alert=True
            )
            return

        dispatch = MailDispatchRepository(self._session)
        visibility = await dispatch.message_visibility(message_id)
        if visibility is None:
            await bot.answer_callback_query(
                callback_query_id, text="Сообщение больше не доступно.", show_alert=True
            )
            return
        team_id, _ = visibility
        sees_all = permissions_subset(full_catalog_permissions(), dict(user.role.permissions))
        member = team_id is not None and await links.is_team_member(
            user_id=user.id, team_id=team_id
        )
        if not (sees_all or member):
            await bot.answer_callback_query(
                callback_query_id, text="Сообщение недоступно.", show_alert=True
            )
            return

        await self._send_message_body(
            bot=bot,
            chat_id=chat_id,
            callback_query_id=callback_query_id,
            message_id=message_id,
        )

    async def handle_push_callback(
        self,
        *,
        bot_config: MailPushBot,
        callback_query_id: str,
        telegram_user_id: int,
        chat_id: int,
        data: str,
    ) -> None:
        """Callback push-бота команды (ADR-044 §9): авторизация admin + team-match.

        Права = членство в MAIL_ADMIN_TELEGRAM_IDS (from.id подписан Telegram). DEFENSIVE
        team-match: письмо обязано принадлежать команде ЭТОГО бота (`team_id == bot.team_id`)
        — админ команды X не вытянет письмо команды Y подделкой `mail:{id}`. Ответ — токеном
        этого бота. Никогда не бросает.
        """
        bot = MailBotClient(bot_config.token, self._settings.mail_bot_proxy_url)
        match = _CALLBACK_PATTERN.match(data or "")
        if match is None:
            await bot.answer_callback_query(callback_query_id, text="Неподдерживаемое действие.")
            return
        message_id = int(match.group(1))

        if telegram_user_id not in self._settings.mail_admin_telegram_ids_list:
            await bot.answer_callback_query(callback_query_id, text="Нет доступа.", show_alert=True)
            return

        dispatch = MailDispatchRepository(self._session)
        visibility = await dispatch.message_visibility(message_id)
        if visibility is None:
            await bot.answer_callback_query(
                callback_query_id, text="Сообщение больше не доступно.", show_alert=True
            )
            return
        team_id, _ = visibility
        if team_id != bot_config.team_id:
            await bot.answer_callback_query(
                callback_query_id, text="Сообщение недоступно.", show_alert=True
            )
            return

        await self._send_message_body(
            bot=bot,
            chat_id=chat_id,
            callback_query_id=callback_query_id,
            message_id=message_id,
        )

    async def get_settings(self, user_id: uuid.UUID) -> MailUserSettingsResponse:
        """Текущее состояние opt-out (нет строки → включено)."""
        stored = await MailUserSettingsRepository(self._session).get(user_id)
        enabled = True if stored is None else stored
        return MailUserSettingsResponse(tg_notifications_enabled=enabled)

    async def update_settings(
        self, *, user_id: uuid.UUID, enabled: bool
    ) -> MailUserSettingsResponse:
        """Установить opt-out уведомлений (upsert по `principal.user_id`, ADR-044 §2)."""
        repo = MailUserSettingsRepository(self._session)
        await repo.upsert(user_id=user_id, enabled=enabled)
        await self._session.commit()
        logger.info("mail_user_settings_updated", user_id=str(user_id))
        return MailUserSettingsResponse(tg_notifications_enabled=enabled)


__all__ = ["MailTelegramService"]
