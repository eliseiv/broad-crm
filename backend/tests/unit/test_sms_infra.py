"""Unit-тесты инфраструктуры «СМС» (twilio_security, sms_telegram, format_sms_message).

- `validate_twilio_signature` — реальный `RequestValidator` (валидная/битая/отсутствующая
  подпись). Секрет не логируется.
- `SmsBotClient` — httpx.MockTransport через подмену httpx.AsyncClient: успех/403(dead)/
  forbidden-маркер/ретраибельная ошибка/сетевой сбой/битый JSON; is_configured; setWebhook.
- `format_sms_message`/`_split_message` — нормативный формат и разбиение длинных.
"""

from __future__ import annotations

import httpx
import pytest
from app.infra import sms_telegram as sms_tg_module
from app.infra.sms_telegram import (
    SmsBotClient,
    TelegramApiError,
    TelegramForbiddenError,
)
from app.infra.twilio_security import validate_twilio_signature
from twilio.request_validator import RequestValidator

_AUTH_TOKEN = "twilio-secret-auth-token"
_URL = "https://crm.example.com/api/sms/webhooks/twilio/sms"
_FORM = {"MessageSid": "SM123", "From": "+79161234567", "To": "+13105551234", "Body": "hi"}


# --- validate_twilio_signature ----------------------------------------------


def test_twilio_signature_valid() -> None:
    signature = RequestValidator(_AUTH_TOKEN).compute_signature(_URL, _FORM)
    assert validate_twilio_signature(
        auth_token=_AUTH_TOKEN, signature=signature, url=_URL, form_data=_FORM
    )


def test_twilio_signature_invalid() -> None:
    assert not validate_twilio_signature(
        auth_token=_AUTH_TOKEN, signature="clearly-wrong", url=_URL, form_data=_FORM
    )


def test_twilio_signature_missing_is_false() -> None:
    assert not validate_twilio_signature(
        auth_token=_AUTH_TOKEN, signature=None, url=_URL, form_data=_FORM
    )


def test_twilio_signature_tampered_url_is_false() -> None:
    signature = RequestValidator(_AUTH_TOKEN).compute_signature(_URL, _FORM)
    assert not validate_twilio_signature(
        auth_token=_AUTH_TOKEN,
        signature=signature,
        url="https://evil.example.com/api/sms/webhooks/twilio/sms",
        form_data=_FORM,
    )


# --- SmsBotClient -----------------------------------------------------------


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(sms_tg_module.httpx, "AsyncClient", factory)


def test_bot_is_configured() -> None:
    assert SmsBotClient("token").is_configured is True
    assert SmsBotClient("").is_configured is False
    assert SmsBotClient("   ").is_configured is False


async def test_send_message_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    result = await SmsBotClient("BOT-TOKEN").send_message(
        555, "текст", reply_markup={"inline_keyboard": []}
    )
    assert result["ok"] is True
    assert captured["path"] == "/botBOT-TOKEN/sendMessage"
    assert captured["body"] == {
        "chat_id": 555,
        "text": "текст",
        "reply_markup": {"inline_keyboard": []},
    }


async def test_send_message_403_raises_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(403, json={"ok": False, "description": "x"})),
    )
    with pytest.raises(TelegramForbiddenError):
        await SmsBotClient("t").send_message(1, "hi")


async def test_send_message_forbidden_marker_raises_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HTTP 400, но описание содержит forbidden-маркер → линк мёртв (mark_dead).
    _install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda _r: httpx.Response(400, json={"ok": False, "description": "chat not found"})
        ),
    )
    with pytest.raises(TelegramForbiddenError):
        await SmsBotClient("t").send_message(1, "hi")


async def test_send_message_5xx_raises_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(500, json={"ok": False, "description": "e"})),
    )
    with pytest.raises(TelegramApiError) as exc:
        await SmsBotClient("t").send_message(1, "hi")
    assert not isinstance(exc.value, TelegramForbiddenError)


async def test_send_message_network_error_raises_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(TelegramApiError):
        await SmsBotClient("t").send_message(1, "hi")


async def test_send_message_invalid_json_raises_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, content=b"not-json")),
    )
    with pytest.raises(TelegramApiError):
        await SmsBotClient("t").send_message(1, "hi")


async def test_set_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    result = await SmsBotClient("t").set_webhook(url="https://x/hook", secret_token="s3cret")
    assert result["ok"] is True
    assert captured["path"] == "/bott/setWebhook"
    assert captured["body"] == {"url": "https://x/hook", "secret_token": "s3cret"}


async def test_set_my_commands_error_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(400, json={"ok": False, "description": "e"})),
    )
    with pytest.raises(TelegramApiError):
        await SmsBotClient("t").set_my_commands([{"command": "start", "description": "s"}])


# --- format_sms_message / _split_message ------------------------------------


def test_format_sms_message_normative_format() -> None:
    from datetime import UTC, datetime

    from app.models.sms_inbound import SmsInbound
    from app.services.sms_ingest_service import format_sms_message

    sms = SmsInbound(
        twilio_message_sid="SM1",
        from_number="+79161234567",
        to_number="+13105551234",
        body="Ваш код: 123456",
        team_id=None,
        raw_payload={},
        received_at=datetime(2026, 7, 9, 14, 5, tzinfo=UTC),
    )
    text = format_sms_message(sms)
    assert text.startswith("📩 Новое SMS")
    assert "📱 Номер: +13105551234" in text
    assert "👤 От: +79161234567" in text
    assert "💬 Текст: Ваш код: 123456" in text
    assert "🕒 Время: 09.07 14:05" in text


def test_split_message_short_single_part() -> None:
    from app.services.sms_ingest_service import _split_message

    assert _split_message("короткое") == ["короткое"]


def test_split_message_long_is_chunked_within_limit() -> None:
    from app.services.sms_ingest_service import _split_message

    text = "\n".join(["строка" * 100 for _ in range(20)])  # заведомо > 3500
    parts = _split_message(text, limit=500)
    assert len(parts) > 1
    assert all(len(p) <= 500 for p in parts)


def test_split_message_single_oversized_line_hard_split() -> None:
    from app.services.sms_ingest_service import _split_message

    parts = _split_message("A" * 1200, limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "".join(parts) == "A" * 1200
