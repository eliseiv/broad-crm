"""Unit-тесты httpx-клиента почты `app/infra/mail_client.py` (modules/mail, ADR-012).

Внешний сервис `postapp.store` НЕ вызывается вживую: httpx-граница замокана через
`httpx.MockTransport` (паттерн `test_ai_provider.py`/`test_infra_health_logging.py`).
Проверяются: секрет `MAIL_API_KEY` только в заголовке `X-API-Key` и не в URL/логах
(05-security.md); идемпотентность ретраев (list ретраит транзиентные; reply НЕ
ретраит read-timeout/5xx — защита от двойной отправки); маппинг внешних кодов в
типизированные исключения модуля; несовместимое тело → MailUnavailable.
"""

from __future__ import annotations

import httpx
import pytest
import structlog
from app.infra import mail_client as mod
from app.infra.mail_client import (
    MailClient,
    MailMessageNotFound,
    MailRejected,
    MailUnavailable,
)

KEY = "mail-secret-KEY-abc123XYZ"

_VALID_LIST = {"messages": [], "next_since_id": None, "has_more": False}
_VALID_REPLY = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}


def _install(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    """Подменяет httpx.AsyncClient на клиент с MockTransport и глушит backoff-sleep."""
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)


def _client() -> MailClient:
    return MailClient(base_url="https://postapp.store", api_key=KEY, timeout_sec=5)


# --------------------------------------------------------- секрет: заголовок, не в URL
async def test_list_sends_api_key_header_and_key_not_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["x_api_key"] = request.headers.get("x-api-key")
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))

    result = await _client().list_messages(since_id=100, limit=50)

    assert result == _VALID_LIST
    assert captured["x_api_key"] == KEY  # ключ — только в X-API-Key
    assert captured["authorization"] is None
    assert KEY not in str(captured["url"])  # ключ не попадает в URL/query


async def test_reply_sends_api_key_header_and_key_not_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["x_api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=_VALID_REPLY)

    _install(monkeypatch, httpx.MockTransport(handler))

    result = await _client().reply(message_id=42, payload={"body": "текст"})

    assert result == _VALID_REPLY
    assert captured["x_api_key"] == KEY
    assert KEY not in str(captured["url"])


async def test_list_forwards_since_id_and_limit_params(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["since_id"] = request.url.params.get("since_id")
        captured["limit"] = request.url.params.get("limit")
        captured["path"] = request.url.path
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _client().list_messages(since_id=777, limit=25)

    assert captured["path"] == "/api/external/messages"
    assert captured["since_id"] == "777"
    assert captured["limit"] == "25"


async def test_list_omits_since_id_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["has_since"] = "since_id" in request.url.params
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _client().list_messages(since_id=None, limit=50)

    assert captured["has_since"] is False


# ----------------------------------------------- list (GET): ретраит транзиентные
async def test_list_retries_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().list_messages(since_id=None, limit=50)

    assert result == _VALID_LIST
    assert calls["n"] == 3  # 1 попытка + 2 ретрая


async def test_list_exhausts_5xx_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().list_messages(since_id=None, limit=50)

    assert calls["n"] == 3


async def test_list_retries_read_timeout_then_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().list_messages(since_id=None, limit=50)

    assert calls["n"] == 3  # read-timeout ретраится для идемпотентного GET


async def test_list_retries_connect_error_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().list_messages(since_id=None, limit=50)

    assert result == _VALID_LIST
    assert calls["n"] == 2


# ----------------------- reply (POST): НЕ ретраит read-timeout/5xx (без двойной отправки)
async def test_reply_does_not_retry_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().reply(message_id=1, payload={"body": "x"})

    # Ровно одна отправка: письмо могло быть принято — повтор недопустим.
    assert calls["n"] == 1


async def test_reply_does_not_retry_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().reply(message_id=1, payload={"body": "x"})

    assert calls["n"] == 1


async def test_reply_retries_connect_error_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # Соединение не установлено → запрос не отправлен → повтор безопасен.
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, json=_VALID_REPLY)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().reply(message_id=1, payload={"body": "x"})

    assert result == _VALID_REPLY
    assert calls["n"] == 2


async def test_reply_exhausts_connect_error_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("conn refused", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().reply(message_id=1, payload={"body": "x"})

    assert calls["n"] == 3


# ------------------------------------------------------------------ маппинг статусов
async def test_404_raises_message_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, httpx.MockTransport(lambda _r: httpx.Response(404, json={"d": "no"})))
    with pytest.raises(MailMessageNotFound):
        await _client().reply(message_id=999, payload={"body": "x"})


@pytest.mark.parametrize("status_code", [400, 409, 422])
async def test_other_4xx_raises_rejected(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    _install(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(status_code, json={"d": "bad"})),
    )
    with pytest.raises(MailRejected):
        await _client().reply(message_id=1, payload={"body": "x"})


async def test_non_json_body_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, content=b"not-json")),
    )
    with pytest.raises(MailUnavailable):
        await _client().list_messages(since_id=None, limit=50)


async def test_non_dict_json_body_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, json=[1, 2, 3])),
    )
    with pytest.raises(MailUnavailable):
        await _client().list_messages(since_id=None, limit=50)


async def test_generic_http_error_maps_unavailable_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.HTTPError("unexpected protocol error")

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().list_messages(since_id=None, limit=50)

    assert calls["n"] == 1  # прочая ошибка httpx неретраябельна


# ------------------------------------------------------------ секрет не логируется
async def test_api_key_not_logged_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """При ошибке клиент логирует событие, но НИКОГДА не пишет ключ (05-security.md)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _install(monkeypatch, httpx.MockTransport(handler))

    # Модульный логгер mail_client мог быть закеширован ранее (cache_logger_on_first_use)
    # на устаревший список процессоров — тогда capture_logs() не перехватит событие.
    # Пересобираем свежий proxy на текущий (сброшенный autouse-фикстурой) конфиг, чтобы
    # тест был устойчив к порядку выполнения в полном наборе.
    monkeypatch.setattr(mod, "logger", structlog.get_logger(mod.__name__))

    with structlog.testing.capture_logs() as logs, pytest.raises(MailUnavailable):
        await _client().list_messages(since_id=None, limit=50)

    assert logs  # событие(я) записаны
    serialized = repr(logs)
    assert KEY not in serialized
