"""Unit-тесты KnowledgeBotClient (ADR-076): sendMessage без parse_mode, 403→Forbidden."""

from __future__ import annotations

import json

import httpx
import pytest
from app.infra import knowledge_bot_telegram as kb_module
from app.infra.knowledge_bot_telegram import (
    KnowledgeBotClient,
    TelegramApiError,
    TelegramForbiddenError,
)

TOKEN = "999:KB-SECRET-TOKEN"


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(kb_module.httpx, "AsyncClient", factory)


async def test_send_message_url_payload_without_parse_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["host"] = request.url.host
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = KnowledgeBotClient(TOKEN)

    payload = await client.send_message(42, "привет")

    assert payload["ok"] is True
    assert captured["host"] == "api.telegram.org"
    assert captured["path"] == f"/bot{TOKEN}/sendMessage"
    assert captured["body"] == {"chat_id": 42, "text": "привет"}
    assert "parse_mode" not in captured["body"]  # type: ignore[operator]


async def test_http_403_raises_telegram_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda _r: httpx.Response(
                403,
                json={"ok": False, "description": "Forbidden: bot was blocked by the user"},
            )
        ),
    )
    client = KnowledgeBotClient(TOKEN)

    with pytest.raises(TelegramForbiddenError):
        await client.send_message(7, "hi")


async def test_forbidden_marker_without_403_raises_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda _r: httpx.Response(
                400,
                json={"ok": False, "description": "Bad Request: chat not found"},
            )
        ),
    )
    client = KnowledgeBotClient(TOKEN)

    with pytest.raises(TelegramForbiddenError):
        await client.send_message(8, "hi")


async def test_other_error_raises_telegram_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(
        monkeypatch,
        httpx.MockTransport(
            lambda _r: httpx.Response(500, json={"ok": False, "description": "internal"})
        ),
    )
    client = KnowledgeBotClient(TOKEN)

    with pytest.raises(TelegramApiError) as exc:
        await client.send_message(9, "hi")
    assert not isinstance(exc.value, TelegramForbiddenError)


async def test_network_error_raises_telegram_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install_transport(monkeypatch, httpx.MockTransport(handler))
    client = KnowledgeBotClient(TOKEN)

    with pytest.raises(TelegramApiError) as exc:
        await client.send_message(1, "hi")
    assert not isinstance(exc.value, TelegramForbiddenError)


def test_empty_token_is_not_configured() -> None:
    assert KnowledgeBotClient("").is_configured is False
    assert KnowledgeBotClient("  ").is_configured is False
    assert KnowledgeBotClient(TOKEN).is_configured is True
