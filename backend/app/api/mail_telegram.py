"""Публичные Telegram-эндпоинты почты (ADR-044 §6/§7/§9). CSRF/JWT-exempt.

- `POST /api/mail/telegram/webhook/{secret}` — основной бот `@ba_mail_bot`: `/start`
  (самопривязка) + callback «Посмотреть сообщение». Секрет — URL-сегмент (constant-time)
  + опц. заголовок `X-Telegram-Bot-Api-Secret-Token`; mismatch → 404 (анти-энумерация).
- `POST /api/mail/telegram/push-webhook/{bot_name}` — push-боты: секрет header-only
  fail-closed (missing/mismatch → 404); только `callback_query`.
- `POST /api/mail/telegram/auth` — Mini App `/tg/mail` SSO (HMAC `initData` — граница
  безопасности); валидная подпись → CRM access-JWT, не сопоставлен → 403.

Секреты и сырое тело апдейта/initData (PII) не логируются.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.deps import MailTelegramServiceDep, SettingsDep
from app.logging import get_logger
from app.schemas.mail_telegram import MailTelegramAuthRequest, MailTelegramAuthResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/mail/telegram", tags=["mail-telegram"])

_BOT_API_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@dataclass(frozen=True, slots=True)
class _StartUpdate:
    chat_id: int
    username: str | None


@dataclass(frozen=True, slots=True)
class _CallbackUpdate:
    callback_query_id: str
    telegram_user_id: int
    chat_id: int
    data: str


def _secret_matches(provided: str, expected: str) -> bool:
    """Constant-time сравнение; пустой ожидаемый секрет → всегда False (fail-closed)."""
    if not expected:
        return False
    return secrets.compare_digest(provided, expected)


def _extract_start(update: dict[str, Any]) -> _StartUpdate | None:
    """`/start`-апдейт → (chat_id, username) или None."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str) or text.strip().split()[0:1] != ["/start"]:
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
        return None
    sender = message.get("from")
    username = sender.get("username") if isinstance(sender, dict) else None
    return _StartUpdate(
        chat_id=int(chat["id"]),
        username=username if isinstance(username, str) else None,
    )


def _extract_callback(update: dict[str, Any]) -> _CallbackUpdate | None:
    """`callback_query`-апдейт → поля доставки или None."""
    query = update.get("callback_query")
    if not isinstance(query, dict):
        return None
    callback_id = query.get("id")
    sender = query.get("from")
    data = query.get("data")
    message = query.get("message")
    if not isinstance(callback_id, str) or not isinstance(sender, dict):
        return None
    telegram_user_id = sender.get("id")
    if not isinstance(telegram_user_id, int):
        return None
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
        return None
    return _CallbackUpdate(
        callback_query_id=callback_id,
        telegram_user_id=int(telegram_user_id),
        chat_id=int(chat["id"]),
        data=data if isinstance(data, str) else "",
    )


async def _parse_update(request: Request) -> dict[str, Any] | None:
    """Разобрать тело апдейта; битый JSON/не-объект → None (обработчик отвечает 200)."""
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


@router.post("/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    service: MailTelegramServiceDep,
    settings: SettingsDep,
) -> Response:
    """Апдейты основного бота (ADR-044 §6). Секрет-mismatch → 404 (анти-энумерация).

    `/start` → самопривязка + приветствие; `callback_query` `mail:{id}` → отправка тела;
    прочее → 200 no-op. Всегда 200 при валидном секрете (Telegram снимает апдейт с ретрая).
    """
    expected = settings.mail_bot_webhook_secret
    if not _secret_matches(secret, expected):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    header_secret = request.headers.get(_BOT_API_SECRET_HEADER, "")
    if header_secret and not _secret_matches(header_secret, expected):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    update = await _parse_update(request)
    if update is None:
        return Response(status_code=status.HTTP_200_OK)

    callback = _extract_callback(update)
    if callback is not None:
        await service.handle_callback(
            callback_query_id=callback.callback_query_id,
            telegram_user_id=callback.telegram_user_id,
            chat_id=callback.chat_id,
            data=callback.data,
        )
        return Response(status_code=status.HTTP_200_OK)

    start = _extract_start(update)
    if start is not None:
        await service.handle_start(telegram_user_id=start.chat_id, username=start.username)
    return Response(status_code=status.HTTP_200_OK)


@router.post("/push-webhook/{bot_name}")
async def telegram_push_webhook(
    bot_name: str,
    request: Request,
    service: MailTelegramServiceDep,
    settings: SettingsDep,
) -> Response:
    """Апдейты push-бота команды (ADR-044 §9). Секрет header-only fail-closed → 404.

    Обрабатывает только `callback_query` (push-боты не лончеры); прочее → 200 no-op.
    Неизвестный/несконфигурированный `bot_name` и неверный секрет → 404 (неотличимы,
    STRIDE-S).
    """
    bot_config = next(
        (b for b in settings.mail_push_bots if b.name == bot_name and b.webhook_secret),
        None,
    )
    if bot_config is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    header_secret = request.headers.get(_BOT_API_SECRET_HEADER, "")
    if not _secret_matches(header_secret, bot_config.webhook_secret):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    update = await _parse_update(request)
    if update is None:
        return Response(status_code=status.HTTP_200_OK)

    callback = _extract_callback(update)
    if callback is None:
        return Response(status_code=status.HTTP_200_OK)
    await service.handle_push_callback(
        bot_config=bot_config,
        callback_query_id=callback.callback_query_id,
        telegram_user_id=callback.telegram_user_id,
        chat_id=callback.chat_id,
        data=callback.data,
    )
    return Response(status_code=status.HTTP_200_OK)


@router.post("/auth", response_model=MailTelegramAuthResponse)
async def telegram_auth(
    payload: MailTelegramAuthRequest,
    service: MailTelegramServiceDep,
) -> MailTelegramAuthResponse:
    """Беспарольный Telegram-SSO Mini App `/tg/mail` (ADR-044 §7). CSRF/JWT-exempt.

    Валидная подпись `initData` → резолв пользователя → CRM access-JWT; не сопоставлен →
    403 mail_operator_not_provisioned; плохой HMAC → 401 invalid_init_data; протухший →
    401 init_data_expired; пустой initData → 400 validation_error.
    """
    return await service.auth(payload.init_data)
