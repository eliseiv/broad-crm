"""Integration-тесты публичного Twilio-webhook приёма SMS (04-api.md, 05-security.md).

`POST /api/sms/webhooks/twilio/sms`: валидная подпись → 200 `<Response></Response>`
(SMS сохранён); неверная/отсутствующая → 401 invalid_twilio_signature; VERIFY=true без
TWILIO_AUTH_TOKEN → 503 twilio_not_configured. URL для подписи строится ТОЛЬКО из
SMS_PUBLIC_BASE_URL (не из Host/X-Forwarded-*): валидная подпись проходит даже при
чужом Host-заголовке.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.models.sms_inbound import SmsInbound
from sms_helpers import build_app, build_principal, client, sms_db
from sqlalchemy import select
from twilio.request_validator import RequestValidator

_AUTH_TOKEN = "twilio-auth-token-secret"
_BASE_URL = "https://crm.example.com"
_PATH = "/api/sms/webhooks/twilio/sms"
_FORM = {
    "MessageSid": "SM0001",
    "From": "+79161234567",
    "To": "+13105559999",
    "Body": "Ваш код 123",
}


def _set_twilio_env(monkeypatch: Any, *, verify: bool = True, with_token: bool = True) -> None:
    from app.config import get_settings

    monkeypatch.setenv("VERIFY_TWILIO_SIGNATURE", "true" if verify else "false")
    monkeypatch.setenv("SMS_PUBLIC_BASE_URL", _BASE_URL)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", _AUTH_TOKEN if with_token else "")
    monkeypatch.setenv("SMS_TELEGRAM_BOT_TOKEN", "")  # бот не сконфигурирован → без fan-out
    get_settings.cache_clear()


def _signature() -> str:
    return RequestValidator(_AUTH_TOKEN).compute_signature(_BASE_URL + _PATH, _FORM)


def _headers(signature: str | None, *, host: str = "crm.example.com") -> dict[str, str]:
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Host": host}
    if signature is not None:
        headers["X-Twilio-Signature"] = signature
    return headers


async def test_valid_signature_accepts_and_saves(monkeypatch: Any) -> None:
    _set_twilio_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(_PATH, content=urlencode(_FORM), headers=_headers(_signature()))
        async with sm() as s:
            saved = (
                await s.execute(select(SmsInbound).where(SmsInbound.twilio_message_sid == "SM0001"))
            ).scalar_one()

    assert resp.status_code == 200
    assert resp.text == "<Response></Response>"
    assert saved.to_number == "+13105559999"
    assert saved.team_id is None  # неизвестный номер → снимок команды пуст


async def test_url_for_signature_from_base_not_host_header(monkeypatch: Any) -> None:
    # Подпись посчитана для SMS_PUBLIC_BASE_URL; запрос идёт с чужим Host → всё равно 200
    # (Host/X-Forwarded-* для подписи не используются).
    _set_twilio_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                _PATH, content=urlencode(_FORM), headers=_headers(_signature(), host="evil.example")
            )
    assert resp.status_code == 200


async def test_invalid_signature_is_401(monkeypatch: Any) -> None:
    _set_twilio_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                _PATH, content=urlencode(_FORM), headers=_headers("wrong-signature")
            )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_twilio_signature"


async def test_missing_signature_is_401(monkeypatch: Any) -> None:
    _set_twilio_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(_PATH, content=urlencode(_FORM), headers=_headers(None))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_twilio_signature"


async def test_verify_true_without_token_is_503(monkeypatch: Any) -> None:
    _set_twilio_env(monkeypatch, with_token=False)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(_PATH, content=urlencode(_FORM), headers=_headers("any"))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "twilio_not_configured"


async def test_verify_disabled_accepts_without_signature(monkeypatch: Any) -> None:
    _set_twilio_env(monkeypatch, verify=False)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(_PATH, content=urlencode(_FORM), headers=_headers(None))
    assert resp.status_code == 200
    assert resp.text == "<Response></Response>"
