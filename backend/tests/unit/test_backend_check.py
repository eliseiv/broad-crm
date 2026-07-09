"""Unit-тесты проверки доступности бэка (modules/backends#нормализация-домена-и-проверка).

Чистые функции нормализации/валидации домена и сборки URL — без сети. Маппинг
`check_backend` проверяется с httpx.MockTransport, инжектируемым подменой
httpx.AsyncClient в модуле backend_check (по образцу test_proxy_check):
  - строго `2xx` → working; `3xx`/`4xx`/`5xx` → «Ошибка бэка (HTTP N)»;
  - таймаут (после ретраев) → «Таймаут подключения»;
  - TransportError/ConnectError (после ретраев) → «Бэк недоступен»;
  - прочая httpx.HTTPError → «Ошибка бэка» (без ретраев);
  - follow_redirects=False (3xx не следуется), URL = `https://{domain}/health`.
Без реальной сети.
"""

from __future__ import annotations

import httpx
import pytest
from app.infra import backend_check as mod
from app.infra.backend_check import (
    REASON_BACKEND_ERROR,
    REASON_TIMEOUT,
    REASON_UNREACHABLE,
    build_health_url,
    check_backend,
    is_valid_domain,
    normalize_domain,
)


def _install(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    """Подменяет httpx.AsyncClient в backend_check на клиент с MockTransport и глушит sleep."""
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


# ---------------------------------------------------------------- normalize_domain
# ADR-042: normalize_domain → канон `https://<host>/` (host lowercased, port сохранён).
def test_normalize_strips_https_scheme_and_path() -> None:
    assert normalize_domain("https://api.example.com/") == "https://api.example.com/"


def test_normalize_strips_http_scheme_and_path() -> None:
    assert normalize_domain("http://x/health") == "https://x/"


def test_normalize_lowercases_host_keeps_port() -> None:
    assert normalize_domain("API.Example.com:8443") == "https://api.example.com:8443/"


def test_normalize_trims_surrounding_whitespace() -> None:
    assert normalize_domain("  api.example.com  ") == "https://api.example.com/"


def test_normalize_scheme_case_insensitive() -> None:
    assert normalize_domain("HTTPS://API.EXAMPLE.COM/path?q=1") == "https://api.example.com/"


def test_normalize_without_scheme_is_lowercased() -> None:
    assert normalize_domain("Sub.Domain.Example.Com") == "https://sub.domain.example.com/"


def test_normalize_drops_query_and_fragment_via_first_slash() -> None:
    assert normalize_domain("host/a/b?c#d") == "https://host/"


def test_normalize_no_double_scheme_regression() -> None:
    # Анти-регресс ADR-042: канон + build_health_url НЕ дают `https://https://`.
    canon = normalize_domain("https://lumorixsite.shop")
    assert canon == "https://lumorixsite.shop/"
    assert build_health_url(canon) == "https://lumorixsite.shop/health"
    assert "https://https://" not in build_health_url(canon)


# ------------------------------------------------------------------ is_valid_domain
@pytest.mark.parametrize(
    "domain",
    [
        "api.example.com",
        "x",
        "sub.domain.example.com",
        "api.example.com:8443",
        "host:1",
        "host:65535",
        "a-b.example.com",
    ],
)
def test_valid_domains(domain: str) -> None:
    assert is_valid_domain(domain) is True


@pytest.mark.parametrize(
    "domain",
    [
        "",  # пустой
        "api example.com",  # пробел
        "host:0",  # порт < 1
        "host:65536",  # порт > 65535
        "host:99999",  # порт вне диапазона
        "host:abc",  # нечисловой порт
        "host:",  # пустой порт
        ":8443",  # пустой host при валидном порте
        "-host.com",  # метка начинается с дефиса
        "host-.com",  # метка заканчивается дефисом
        ".example.com",  # пустая метка
        "example..com",  # двойная точка
    ],
)
def test_invalid_domains(domain: str) -> None:
    assert is_valid_domain(domain) is False


def test_normalize_then_validate_examples_from_spec() -> None:
    # Примеры из modules/backends: после нормализации → валидны.
    assert is_valid_domain(normalize_domain("https://api.example.com/")) is True
    assert is_valid_domain(normalize_domain("API.Example.com:8443")) is True
    assert is_valid_domain(normalize_domain("http://x/health")) is True


# ------------------------------------------------------------------ build_health_url
def test_build_health_url_scheme_and_path_fixed() -> None:
    # build_health_url принимает КАНОН `https://<host>/` и дописывает `health`.
    assert build_health_url("https://api.example.com/") == "https://api.example.com/health"


def test_build_health_url_keeps_port() -> None:
    assert (
        build_health_url("https://api.example.com:8443/") == "https://api.example.com:8443/health"
    )


# ------------------------------------------------------ маппинг статусов check_backend
@pytest.mark.parametrize("status_code", [200, 201, 204, 299])
async def test_strict_2xx_is_working(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    _install(monkeypatch, _static(status_code))
    result = await check_backend("https://api.example.com/")
    assert result.outcome == "working"
    assert result.reason is None


@pytest.mark.parametrize("status_code", [300, 301, 302, 399])
async def test_3xx_is_backend_error_not_working(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    # В отличие от прокси, редиректы не принимаются: 3xx = ошибка здоровья.
    _install(monkeypatch, _static(status_code))
    result = await check_backend("https://api.example.com/")
    assert result.outcome == "error"
    assert result.reason == f"Ошибка бэка (HTTP {status_code})"


@pytest.mark.parametrize("status_code", [400, 403, 404, 500, 502, 503])
async def test_non_2xx_is_backend_error_with_status_code(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _install(monkeypatch, _static(status_code))
    result = await check_backend("https://api.example.com/")
    assert result.outcome == "error"
    assert result.reason == f"Ошибка бэка (HTTP {status_code})"


# ------------------------------------------------------ таймаут (после ретраев)
async def test_timeout_exhausts_retries_then_timeout_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_backend("https://api.example.com/")

    assert result.outcome == "error"
    assert result.reason == REASON_TIMEOUT
    assert calls["n"] == 3  # 1 попытка + 2 ретрая


# --------------------------------------------- транспортная ошибка (после ретраев)
async def test_connect_error_exhausts_retries_then_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("conn refused", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_backend("https://api.example.com/")

    assert result.outcome == "error"
    assert result.reason == REASON_UNREACHABLE
    assert calls["n"] == 3


async def test_transport_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    # ConnectError ⊂ TransportError → «Бэк недоступен» (DNS/TLS/сеть).
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("tls handshake failed", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_backend("https://api.example.com/")

    assert result.outcome == "error"
    assert result.reason == REASON_UNREACHABLE


# ------------------------------------------------- прочая ошибка httpx (без ретрая)
async def test_generic_http_error_is_backend_error_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.HTTPError("unexpected protocol error")

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_backend("https://api.example.com/")

    assert result.outcome == "error"
    assert result.reason == REASON_BACKEND_ERROR
    assert calls["n"] == 1  # не транзиентная — без ретраев


# ------------------------------------------------------ ретрай затем восстановление
async def test_transient_then_recovers_to_working(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_backend("https://api.example.com/")

    assert result.outcome == "working"
    assert calls["n"] == 3


async def test_check_hits_health_url_of_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    # Собранный URL проверки — строго `https://{domain}/health`.
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_backend("https://api.example.com:8443/")

    assert result.outcome == "working"
    assert captured["url"] == "https://api.example.com:8443/health"
