"""Unit-тесты проверки ключа у провайдера (modules/ai-keys#проверка-ключа-у-провайдера).

httpx.MockTransport инжектируется подменой httpx.AsyncClient в модуле ai_provider.
Проверяется маппинг статус-кодов → исход (working/error/unknown), корректный URL
`GET /v1/models` и заголовки авторизации (OpenAI Bearer, Anthropic x-api-key +
anthropic-version), детект insufficient_quota, ретраи на 5xx/сети/таймауте и то,
что ключ НЕ попадает в URL. Без реальной сети.
"""

from __future__ import annotations

import httpx
import pytest
from app.infra import ai_provider as mod
from app.infra.ai_provider import (
    REASON_FORBIDDEN,
    REASON_INVALID,
    REASON_PROVIDER,
    REASON_QUOTA,
    check_key,
)
from app.models.ai_key import AiProvider

OPENAI_KEY = "sk-proj-OPENAI-SECRET-bA3T"
ANTHROPIC_KEY = "sk-ant-ANTHROPIC-SECRET-xY9z"


def _install(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    """Подменяет httpx.AsyncClient в ai_provider на клиент с MockTransport и глушит sleep."""
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)


def _static(status_code: int, body: dict | None = None) -> httpx.MockTransport:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body if body is not None else {})

    return httpx.MockTransport(handler)


# ------------------------------------------------------------- OpenAI: URL/заголовки
async def test_openai_request_url_and_bearer_header_key_not_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["host"] = request.url.host
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    _install(monkeypatch, httpx.MockTransport(handler))

    result = await check_key(AiProvider.openai, OPENAI_KEY)

    assert result.outcome == "working"
    assert captured["host"] == "api.openai.com"
    assert captured["path"] == "/v1/models"
    assert captured["auth"] == f"Bearer {OPENAI_KEY}"
    # Ключ уходит ТОЛЬКО в заголовок, не в URL.
    assert OPENAI_KEY not in str(captured["url"])


async def test_anthropic_request_url_and_headers_key_not_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["host"] = request.url.host
        captured["path"] = request.url.path
        captured["x_api_key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    _install(monkeypatch, httpx.MockTransport(handler))

    result = await check_key(AiProvider.anthropic, ANTHROPIC_KEY)

    assert result.outcome == "working"
    assert captured["host"] == "api.anthropic.com"
    assert captured["path"] == "/v1/models"
    assert captured["x_api_key"] == ANTHROPIC_KEY
    assert captured["version"] == "2023-06-01"
    assert captured["auth"] is None  # Anthropic не использует Bearer
    assert ANTHROPIC_KEY not in str(captured["url"])


# ------------------------------------------------------------------- маппинг статусов
@pytest.mark.parametrize("provider", [AiProvider.openai, AiProvider.anthropic])
async def test_200_is_working(monkeypatch: pytest.MonkeyPatch, provider: AiProvider) -> None:
    _install(monkeypatch, _static(200, {"data": []}))
    result = await check_key(provider, "any-key-12345678")
    assert result.outcome == "working"
    assert result.reason is None


@pytest.mark.parametrize("provider", [AiProvider.openai, AiProvider.anthropic])
async def test_401_is_error_invalid(monkeypatch: pytest.MonkeyPatch, provider: AiProvider) -> None:
    _install(monkeypatch, _static(401, {"error": {"message": "bad key"}}))
    result = await check_key(provider, "any-key-12345678")
    assert result.outcome == "error"
    assert result.reason == REASON_INVALID


@pytest.mark.parametrize("provider", [AiProvider.openai, AiProvider.anthropic])
async def test_403_is_error_forbidden(
    monkeypatch: pytest.MonkeyPatch, provider: AiProvider
) -> None:
    _install(monkeypatch, _static(403, {"error": {"message": "forbidden"}}))
    result = await check_key(provider, "any-key-12345678")
    assert result.outcome == "error"
    assert result.reason == REASON_FORBIDDEN


@pytest.mark.parametrize("provider", [AiProvider.openai, AiProvider.anthropic])
async def test_429_insufficient_quota_code_is_quota(
    monkeypatch: pytest.MonkeyPatch, provider: AiProvider
) -> None:
    _install(monkeypatch, _static(429, {"error": {"code": "insufficient_quota"}}))
    result = await check_key(provider, "any-key-12345678")
    assert result.outcome == "error"
    assert result.reason == REASON_QUOTA


async def test_429_insufficient_quota_type_is_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _static(429, {"error": {"type": "insufficient_quota"}}))
    result = await check_key(AiProvider.openai, "any-key-12345678")
    assert result.outcome == "error"
    assert result.reason == REASON_QUOTA


@pytest.mark.parametrize("provider", [AiProvider.openai, AiProvider.anthropic])
async def test_429_rate_limit_without_quota_is_provider_error(
    monkeypatch: pytest.MonkeyPatch, provider: AiProvider
) -> None:
    # 429 rate-limit БЕЗ признака квоты → «Ошибка провайдера», НЕ «Недостаточно средств».
    _install(monkeypatch, _static(429, {"error": {"message": "Rate limit exceeded"}}))
    result = await check_key(provider, "any-key-12345678")
    assert result.outcome == "error"
    assert result.reason == REASON_PROVIDER


async def test_429_unparseable_body_is_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"not-json")

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_key(AiProvider.openai, "any-key-12345678")
    assert result.outcome == "error"
    assert result.reason == REASON_PROVIDER


@pytest.mark.parametrize("status_code", [400, 404, 422])
async def test_other_4xx_is_provider_error(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _install(monkeypatch, _static(status_code, {"error": {"message": "boom"}}))
    result = await check_key(AiProvider.openai, "any-key-12345678")
    assert result.outcome == "error"
    assert result.reason == REASON_PROVIDER


# ------------------------------------------------------- транзиентные ошибки → unknown
async def test_5xx_exhausts_retries_then_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_key(AiProvider.openai, "any-key-12345678")

    assert result.outcome == "unknown"
    assert result.reason is None
    assert calls["n"] == 3  # 1 попытка + 2 ретрая


async def test_5xx_then_recovers_to_working(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"data": []})

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_key(AiProvider.openai, "any-key-12345678")

    assert result.outcome == "working"
    assert calls["n"] == 3


async def test_timeout_exhausts_retries_then_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("timed out", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_key(AiProvider.openai, "any-key-12345678")

    assert result.outcome == "unknown"
    assert calls["n"] == 3


async def test_connect_error_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, json={"data": []})

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_key(AiProvider.openai, "any-key-12345678")

    assert result.outcome == "working"
    assert calls["n"] == 2


async def test_generic_http_error_is_unknown_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.HTTPError("unexpected protocol error")

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await check_key(AiProvider.openai, "any-key-12345678")

    assert result.outcome == "unknown"
    assert calls["n"] == 1  # не транзиентная — без ретраев
