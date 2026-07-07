"""Unit-тесты проверки доступности прокси (modules/proxies#проверка-доступности).

httpx.MockTransport инжектируется подменой httpx.AsyncClient в модуле proxy_check.
Проверяется:
  - сборка URL `scheme://[user[:pass]@]host:port` с URL-энкодингом логина/пароля;
  - маппинг ответа/события → исход: 2xx/3xx → working; таймаут → «Таймаут
    подключения»; TransportError/ProxyError → «Прокси недоступен»; 4xx/5xx/прочая
    HTTPError → «Ошибка прокси»;
  - ограниченные ретраи на транзиентных ошибках (≈3 попытки) до конклюзивного error.
Без реальной сети.
"""

from __future__ import annotations

import httpx
import pytest
from app.infra import proxy_check as mod
from app.infra.proxy_check import (
    REASON_PROXY_ERROR,
    REASON_TIMEOUT,
    REASON_UNREACHABLE,
    build_proxy_url,
    check_proxy,
)


def _install(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    """Подменяет httpx.AsyncClient в proxy_check на клиент с MockTransport и глушит sleep.

    Фабрика игнорирует `proxy=` (реальное соединение не устанавливается) — тестируется
    только маппинг результата, без сети/SOCKS.
    """
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)


def _static(status_code: int) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    return httpx.MockTransport(handler)


# ----------------------------------------------------------------- build_proxy_url
def test_build_url_no_auth() -> None:
    assert build_proxy_url("http", "proxy.example.com", 8080, None, None) == (
        "http://proxy.example.com:8080"
    )


def test_build_url_socks5_scheme_and_port() -> None:
    assert build_proxy_url("socks5", "10.0.0.5", 1080, None, None) == "socks5://10.0.0.5:1080"


def test_build_url_username_only() -> None:
    assert build_proxy_url("http", "host", 3128, "user01", None) == "http://user01@host:3128"


def test_build_url_username_and_password() -> None:
    assert build_proxy_url("https", "host", 443, "user", "pass") == "https://user:pass@host:443"


def test_build_url_encodes_username_and_password() -> None:
    # Спецсимволы логина/пароля URL-энкодятся (safe="") — не ломают структуру URL.
    url = build_proxy_url("http", "host", 8080, "u@se:r", "p@ss/w:d")
    assert url == "http://u%40se%3Ar:p%40ss%2Fw%3Ad@host:8080"


def test_build_url_password_without_username_is_ignored() -> None:
    # Пароль включается только вместе с логином (userinfo строится от username).
    url = build_proxy_url("http", "host", 8080, None, "orphan-pass")
    assert url == "http://host:8080"
    assert "orphan-pass" not in url


# ------------------------------------------------------------------- маппинг статусов
@pytest.mark.parametrize("status_code", [200, 204, 299, 301, 302, 399])
async def test_2xx_3xx_is_working(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    _install(monkeypatch, _static(status_code))
    result = await check_proxy("http", "host", 8080, None, None)
    assert result.outcome == "working"
    assert result.reason is None


@pytest.mark.parametrize("status_code", [400, 403, 404, 500, 502, 503])
async def test_4xx_5xx_is_proxy_error(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    _install(monkeypatch, _static(status_code))
    result = await check_proxy("http", "host", 8080, None, None)
    assert result.outcome == "error"
    assert result.reason == REASON_PROXY_ERROR


# ----------------------------------------------------- таймаут (после ретраев)
async def test_timeout_exhausts_retries_then_timeout_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_proxy("http", "host", 8080, None, None)

    assert result.outcome == "error"
    assert result.reason == REASON_TIMEOUT
    assert calls["n"] == 3  # 1 попытка + 2 ретрая


# ------------------------------- транспортная/прокси-ошибка (после ретраев)
async def test_connect_error_exhausts_retries_then_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("conn refused", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_proxy("socks5", "host", 1080, None, None)

    assert result.outcome == "error"
    assert result.reason == REASON_UNREACHABLE
    assert calls["n"] == 3


async def test_proxy_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    # ProxyError ⊂ TransportError → «Прокси недоступен».
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("proxy handshake failed", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_proxy("socks5", "host", 1080, "u", "p")

    assert result.outcome == "error"
    assert result.reason == REASON_UNREACHABLE


# ------------------------------------------------- прочая ошибка httpx (без ретрая)
async def test_generic_http_error_is_proxy_error_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.HTTPError("unexpected protocol error")

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_proxy("http", "host", 8080, None, None)

    assert result.outcome == "error"
    assert result.reason == REASON_PROXY_ERROR
    assert calls["n"] == 1  # не транзиентная — без ретраев


# ------------------------------------------------------ ретрай затем восстановление
async def test_transient_then_recovers_to_working(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(204)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_proxy("http", "host", 8080, None, None)

    assert result.outcome == "working"
    assert calls["n"] == 3


async def test_check_url_and_password_not_in_url_sent_to_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Эталонный запрос идёт на PROXY_CHECK_URL; пароль в этот URL не попадает
    # (пароль — только в строке proxy=, которая не логируется).
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(204)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_proxy("http", "host", 8080, "user", "s3cr3t")

    assert result.outcome == "working"
    from app.config import get_settings

    assert captured["url"] == get_settings().proxy_check_url
    assert "s3cr3t" not in captured["url"]
