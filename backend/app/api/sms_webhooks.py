"""Публичные эндпоинты модуля «СМС» (04-api.md#sms, 05-security.md). CSRF/JWT-exempt.

Гейтятся криптографически (не JWT/RBAC):
- `POST /api/sms/webhooks/twilio/sms` — подпись `X-Twilio-Signature` (URL из
  `SMS_PUBLIC_BASE_URL` — единственный источник);
- `POST /api/sms/telegram/webhook` — секрет `X-Telegram-Bot-Api-Secret-Token`
  (constant-time до разбора тела);
- `POST /api/sms/telegram/auth` — HMAC `init_data` (беспарольный Telegram-SSO:
  резолв оператора → CRM-JWT + авто-линк, ADR-031).

Секреты и `raw` тело Twilio/Telegram (init_data/Update) не логируются.
"""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import SettingsDep, SmsIngestServiceDep, SmsTelegramLinkServiceDep
from app.errors import invalid_twilio_signature, twilio_not_configured
from app.infra.sms_telegram import SmsBotClient, TelegramApiError
from app.infra.twilio_security import validate_twilio_signature
from app.logging import get_logger
from app.schemas.sms import TelegramAuthRequest, TelegramAuthResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/sms", tags=["sms-webhooks"])

_WELCOME_TEXT = "Добро пожаловать! Откройте приложение по кнопке ниже."


def _public_request_url(settings_base_url: str, request: Request) -> str:
    """URL для проверки подписи Twilio из SMS_PUBLIC_BASE_URL + путь (ADR-030).

    Единственный источник истины; `X-Forwarded-*` для подписи не используется.
    """
    base = settings_base_url.rstrip("/")
    path = request.url.path
    query = request.url.query
    return f"{base}{path}?{query}" if query else f"{base}{path}"


@router.post("/webhooks/twilio/sms")
async def twilio_sms_webhook(
    request: Request,
    ingest: SmsIngestServiceDep,
    settings: SettingsDep,
) -> Response:
    """Приём входящего SMS от Twilio (form-urlencoded). Auth — подпись Twilio.

    Успех → `200 <Response></Response>` (application/xml), включая неизвестный номер
    и дубликат по MessageSid. Неверная/отсутствующая подпись → 401
    invalid_twilio_signature; `VERIFY_TWILIO_SIGNATURE=true` без токена → 503.
    """
    raw_body = await request.body()
    payload = dict(parse_qsl(raw_body.decode("utf-8"), keep_blank_values=True))

    if settings.verify_twilio_signature:
        if not settings.twilio_auth_token:
            raise twilio_not_configured()
        signature = request.headers.get("X-Twilio-Signature")
        if not validate_twilio_signature(
            auth_token=settings.twilio_auth_token,
            signature=signature,
            url=_public_request_url(settings.sms_public_base_url, request),
            form_data=payload,
        ):
            raise invalid_twilio_signature()

    await ingest.handle_incoming_sms(
        twilio_message_sid=payload.get("MessageSid"),
        from_number=payload.get("From", ""),
        to_number=payload.get("To", ""),
        body=payload.get("Body", ""),
        raw_payload=payload,
    )
    return Response(content="<Response></Response>", media_type="application/xml")


def _webapp_markup(webapp_url: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "Открыть приложение", "web_app": {"url": webapp_url}}]]}


def _extract_start_chat_id(update: dict[str, Any]) -> int | None:
    """chat_id, если это message с text == '/start', иначе None."""
    message = update.get("message")
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if not isinstance(text, str) or text.strip() != "/start":
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    return int(chat_id) if isinstance(chat_id, int) else None


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, settings: SettingsDep) -> Response:
    """Апдейты SMS-бота. Auth — секрет `X-Telegram-Bot-Api-Secret-Token` (constant-time).

    Обрабатывает только `/start` → приветствие с кнопкой `web_app`
    (`SMS_TELEGRAM_WEBAPP_URL`). Прочее → 200 no-op. Несовпадение секрета → 403.
    Ошибка `sendMessage` не роняет обработчик (200). Тело/токены не логируются.
    """
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = settings.sms_telegram_webhook_secret
    if not expected or not secrets.compare_digest(provided, expected):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "error": {
                    "code": "invalid_webhook_secret",
                    "message": "Неверный секрет webhook",
                    "details": None,
                }
            },
        )

    try:
        update = await request.json()
    except ValueError:
        return JSONResponse(content={"ok": True})
    if not isinstance(update, dict):
        return JSONResponse(content={"ok": True})

    chat_id = _extract_start_chat_id(update)
    if chat_id is None:
        return JSONResponse(content={"ok": True})  # no-op для прочих апдейтов

    bot = SmsBotClient(settings.sms_telegram_bot_token, settings.sms_telegram_proxy_url)
    if not bot.is_configured:
        logger.warning("sms_tg_webhook_bot_not_configured")
        return JSONResponse(content={"ok": True})

    try:
        await bot.send_message(
            chat_id,
            _WELCOME_TEXT,
            reply_markup=_webapp_markup(settings.sms_telegram_webapp_url),
        )
        logger.info("sms_tg_webhook_start", chat_id=chat_id)
    except TelegramApiError:
        logger.warning("sms_tg_webhook_send_failed", chat_id=chat_id)

    return JSONResponse(content={"ok": True})


@router.post("/telegram/auth", response_model=TelegramAuthResponse)
async def telegram_auth(
    payload: TelegramAuthRequest,
    service: SmsTelegramLinkServiceDep,
) -> TelegramAuthResponse:
    """Публичный беспарольный Telegram-SSO Mini App (HMAC init_data, ADR-031).

    Резолвит CRM-оператора по Telegram-идентичности → авто-upsert/revive линка →
    выдаёт CRM access-JWT (`TelegramAuthResponse`). Не сопоставлен → 403
    sms_operator_not_provisioned; плохой HMAC → 401 invalid_init_data; протухло →
    401 init_data_expired; пустой init_data → 400 validation_error. CSRF/JWT-exempt.
    """
    return await service.auth(payload.init_data)
