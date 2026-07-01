"""Unit-тесты TelegramClient (modules/notifier «Доставка в Telegram»).

httpx.MockTransport инжектируется через подмену httpx.AsyncClient в модуле telegram.
Проверяются: корректный URL/payload, bool-результат без проброса ошибок, ретраи на
429/5xx, отсутствие токена/chat_id/тела в логах.
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.infra import telegram as tg_module
from app.infra.telegram import TelegramClient

TOKEN = "123456:SECRET-BOT-TOKEN-ABCDEF"
CHAT_ID = "CHAT-SECRET-987654"


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    """Подменяет httpx.AsyncClient в модуле telegram на клиент с MockTransport.

    Заодно нейтрализует backoff-паузы, чтобы ретраи не замедляли тест.
    """
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(tg_module.httpx, "AsyncClient", factory)

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(tg_module.asyncio, "sleep", _no_sleep)


async def test_correct_url_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["host"] = request.url.host
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, CHAT_ID)

    ok = await client.send_message("привет")

    assert ok is True
    assert captured["host"] == "api.telegram.org"
    assert captured["path"] == f"/bot{TOKEN}/sendMessage"
    assert captured["body"] == {"chat_id": CHAT_ID, "text": "привет"}


async def test_returns_true_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, json={"ok": True})),
    )
    client = TelegramClient(TOKEN, CHAT_ID)
    assert await client.send_message("hi") is True


async def test_non_retryable_4xx_returns_false_no_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"ok": False, "description": "bad request"})

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, CHAT_ID)

    result = await client.send_message("hi")

    assert result is False  # не пробрасывает наружу
    assert calls["n"] == 1  # 400 не ретраится


async def test_retries_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, CHAT_ID)

    assert await client.send_message("hi") is True
    assert calls["n"] == 3  # 2 ретрая + успех


async def test_retries_on_429_exhausted_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, CHAT_ID)

    assert await client.send_message("hi") is False
    assert calls["n"] == 3  # фиксированный бюджет попыток исчерпан


async def test_retries_on_timeout_then_false(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out", request=request)

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, CHAT_ID)

    assert await client.send_message("hi") is False
    assert calls["n"] == 3


async def test_transport_error_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, json={"ok": True})

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, CHAT_ID)

    assert await client.send_message("hi") is True
    assert calls["n"] == 2


async def test_generic_http_error_returns_false_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.HTTPError("unexpected protocol error")

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = TelegramClient(TOKEN, CHAT_ID)

    assert await client.send_message("hi") is False
    assert calls["n"] == 1  # не транзиентная ошибка — без ретраев


async def test_no_secrets_in_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    # Перехватываем все вызовы логгера и проверяем, что секреты и тело сообщения
    # не передаются в лог-события.
    events: list[tuple[str, dict[str, object]]] = []

    class RecordingLogger:
        def warning(self, event: str, **kw: object) -> None:
            events.append((event, kw))

        def info(self, event: str, **kw: object) -> None:
            events.append((event, kw))

        def error(self, event: str, **kw: object) -> None:
            events.append((event, kw))

    monkeypatch.setattr(tg_module, "logger", RecordingLogger())
    _install_transport(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(403, json={"ok": False})),
    )

    body_text = "BODY-SECRET-PAYLOAD-555"
    client = TelegramClient(TOKEN, CHAT_ID)
    result = await client.send_message(body_text)

    assert result is False
    assert events, "ожидался хотя бы один warning-лог при сбое отправки"
    serialized = json.dumps(events, default=str, ensure_ascii=False)
    assert TOKEN not in serialized
    assert CHAT_ID not in serialized
    assert body_text not in serialized
    assert "api.telegram.org" not in serialized
