"""Unit-тесты httpx-клиента почты `app/infra/mail_client.py` (ADR-044 §1/§4/§8).

Внешний сервис `postapp.store` НЕ вызывается вживую: httpx-граница замокана через
`httpx.MockTransport`. В модели ADR-044 клиент делает ТОЛЬКО управляющие write-вызовы
жизненного цикла ящика (create/update/delete/sync/test) и делегирование reply-отправки
(send). Проверяются: секрет `MAIL_API_KEY` только в заголовке `X-API-Key` и не в URL;
ретрай только на ошибках соединения (connect), НЕ на read-timeout/5xx write (защита от
двойной записи); постатусный маппинг (429/5xx → MailUnavailable; прочий 4xx →
MailRejected со `status_code` + `error_code`); 204/пустое тело → {}; не-объектное тело →
MailUnavailable.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
from app.infra.mail_client import MailClient, MailRejected, MailUnavailable

_BASE = "https://postapp.example"
_KEY = "secret-api-key-value"


def _client_with(handler: Callable[[httpx.Request], httpx.Response]) -> MailClient:
    """MailClient с подменённым httpx-транспортом (MockTransport).

    Подменяет фабрику `httpx.AsyncClient` внутри модуля клиента на клиент с mock-
    транспортом. Восстанавливается autouse-фикстурой `_restore_httpx`.
    """
    import app.infra.mail_client as mod

    orig = mod.httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def _factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        return orig(transport=transport, base_url="")

    mod.httpx.AsyncClient = _factory  # type: ignore[assignment]
    return MailClient(base_url=_BASE, api_key=_KEY, timeout_sec=1.0)


@pytest.fixture(autouse=True)
def _restore_httpx() -> Iterator[None]:
    import app.infra.mail_client as mod

    orig = mod.httpx.AsyncClient
    yield
    mod.httpx.AsyncClient = orig


# --- Секрет только в заголовке, не в URL ------------------------------------
async def test_api_key_in_header_not_in_url() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("X-API-Key")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"id": 5, "is_active": True})

    client = _client_with(handler)
    await client.create_mailbox({"email": "a@b.c"})
    assert captured["auth"] == _KEY
    assert _KEY not in str(captured["url"])


# --- create возвращает присвоенный id --------------------------------------
async def test_create_returns_body() -> None:
    client = _client_with(lambda r: httpx.Response(201, json={"id": 42, "is_active": False}))
    result = await client.create_mailbox({"email": "a@b.c"})
    assert result == {"id": 42, "is_active": False}


# --- 204/пустое тело → {} --------------------------------------------------
async def test_delete_204_returns_empty_dict() -> None:
    client = _client_with(lambda r: httpx.Response(204))
    assert await client.delete_mailbox(7) == {}


# --- Постатусный маппинг: 4xx → MailRejected(status_code, error_code) -------
async def test_409_maps_to_rejected_with_code() -> None:
    client = _client_with(lambda r: httpx.Response(409, json={"error": {"code": "email_taken"}}))
    with pytest.raises(MailRejected) as exc:
        await client.create_mailbox({"email": "a@b.c"})
    assert exc.value.status_code == 409
    assert exc.value.error_code == "email_taken"


async def test_422_maps_to_rejected() -> None:
    client = _client_with(lambda r: httpx.Response(422, json={"detail": "smtp"}))
    with pytest.raises(MailRejected) as exc:
        await client.test_mailbox({"email": "a@b.c"})
    assert exc.value.status_code == 422


# --- 429/5xx → MailUnavailable ---------------------------------------------
async def test_500_maps_to_unavailable() -> None:
    client = _client_with(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(MailUnavailable):
        await client.sync_mailbox(3)


async def test_429_maps_to_unavailable() -> None:
    client = _client_with(lambda r: httpx.Response(429, text="slow down"))
    with pytest.raises(MailUnavailable):
        await client.update_mailbox(3, {"is_active": False})


# --- write НЕ ретраит read-timeout (защита от двойной записи) ---------------
async def test_read_timeout_on_write_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read timeout", request=request)

    client = _client_with(handler)
    with pytest.raises(MailUnavailable):
        await client.create_mailbox({"email": "a@b.c"})
    assert calls["n"] == 1  # ровно одна попытка — write не ретраит read-timeout


# --- connect-ошибка ретраится ----------------------------------------------
async def test_connect_error_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.infra.mail_client as mod

    async def _no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, json={"queued": True})

    client = _client_with(handler)
    result = await client.sync_mailbox(9)
    assert calls["n"] == 2  # первая попытка — connect-ошибка, вторая успешна
    assert result == {"queued": True}


# --- не-объектное тело → MailUnavailable ------------------------------------
async def test_non_object_body_maps_to_unavailable() -> None:
    client = _client_with(lambda r: httpx.Response(200, json=[1, 2, 3]))
    with pytest.raises(MailUnavailable):
        await client.create_mailbox({"email": "a@b.c"})


# --- send делегирует reply --------------------------------------------------
async def test_send_message_posts_to_send_path() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"sent_id": 1, "smtp_message_id": "<m@x>"})

    client = _client_with(handler)
    result = await client.send_message(11, {"to": ["a@b.c"], "body_text": "hi"})
    assert str(captured["path"]).endswith("/api/external/mailboxes/11/send")
    assert result["smtp_message_id"] == "<m@x>"
