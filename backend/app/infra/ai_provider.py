"""Проверка валидности AI-ключа у провайдера через read-only `GET /v1/models`
и hourly credit-probe минимальным inference (ADR-075).

Health (ADR-010): токены не тратятся. Credit-probe: ~1 output token / час.
Ключ НИКОГДА не логируется и не попадает в URL — только в заголовок запроса.
TLS verify включён.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import get_settings
from app.logging import get_logger
from app.models.ai_key import AiProvider

logger = get_logger(__name__)

CheckOutcome = Literal["working", "error", "unknown"]
CreditOutcome = Literal["ok", "depleted", "unknown"]

# Русскоязычные причины ошибки (записываются в error_message, modules/ai-keys).
REASON_INVALID = "Ключ недействителен"
REASON_FORBIDDEN = "Доступ запрещён"
REASON_QUOTA = "Недостаточно средств"
REASON_PROVIDER = "Ошибка провайдера"

# Backoff между попытками на транзиентных ошибках; попыток = len + 1 (т.е. 3).
_BACKOFF_DELAYS_SEC = (0.2, 0.5)
_CREDIT_PROMPT = "ping"


@dataclass(frozen=True)
class KeyCheckResult:
    """Чистый результат проверки: исход + причина (только при `error`)."""

    outcome: CheckOutcome
    reason: str | None


@dataclass(frozen=True)
class CreditProbeResult:
    """Исход hourly credit-probe (ADR-075)."""

    outcome: CreditOutcome
    reason: str | None


def _build_request(provider: AiProvider, api_key: str) -> tuple[str, dict[str, str]]:
    """URL `GET /v1/models` и заголовки авторизации для провайдера.

    Ключ уходит только в заголовок (`Authorization`/`x-api-key`), НЕ в URL.
    """
    settings = get_settings()
    if provider is AiProvider.openai:
        url = f"{settings.openai_api_base.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
    else:  # AiProvider.anthropic
        url = f"{settings.anthropic_api_base.rstrip('/')}/models"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": settings.anthropic_api_version,
        }
    return url, headers


def _is_insufficient_quota(body: dict[str, Any]) -> bool:
    """Детект надёжного признака исчерпания средств у OpenAI (TD-020).

    Матчится ТОЛЬКО точный признак биллинга OpenAI: `error.code == "insufficient_quota"`
    или `error.type == "insufficient_quota"`. Широкий подстрочный матч по
    'quota'/'credit' НЕ используется — иначе rate-limit 429 (например «exceeded your
    quota of requests») ошибочно классифицируется как «Недостаточно средств» вместо
    «Ошибка провайдера». Тело нераспознаваемо / код не совпал → False (трактуется как
    «прочий 4xx» → «Ошибка провайдера»). Anthropic не отражает биллинг в /v1/models,
    поэтому quota-детект к нему не притягивается.
    """
    err = body.get("error")
    if not isinstance(err, dict):
        return False
    return err.get("code") == "insufficient_quota" or err.get("type") == "insufficient_quota"


def _is_credit_depleted(provider: AiProvider, status_code: int, body: dict[str, Any]) -> bool:
    """Детект исчерпания кредитов для credit-probe (ADR-075)."""
    if provider is AiProvider.openai:
        return _is_insufficient_quota(body)
    # Anthropic: billing/credit в error.type / error.message.
    err = body.get("error")
    if isinstance(err, dict):
        typ = str(err.get("type") or "").lower()
        msg = str(err.get("message") or "").lower()
        code = str(err.get("code") or "").lower()
        markers = ("credit", "billing", "balance", "payment", "spend limit", "quota")
        if any(m in typ or m in msg or m in code for m in markers):
            return True
    return status_code == httpx.codes.PAYMENT_REQUIRED  # 402


def _parse_body(response: httpx.Response) -> dict[str, Any]:
    """Безопасно парсит JSON-тело ошибки; нераспознаваемое → пустой dict."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _map_client_error(status_code: int, response: httpx.Response) -> KeyCheckResult:
    """Маппинг 4xx-ответа авторизации/квоты в исход проверки (modules/ai-keys)."""
    if status_code == httpx.codes.UNAUTHORIZED:  # 401
        return KeyCheckResult("error", REASON_INVALID)
    if status_code == httpx.codes.FORBIDDEN:  # 403
        return KeyCheckResult("error", REASON_FORBIDDEN)
    if status_code == httpx.codes.TOO_MANY_REQUESTS:  # 429
        if _is_insufficient_quota(_parse_body(response)):
            return KeyCheckResult("error", REASON_QUOTA)
        return KeyCheckResult("error", REASON_PROVIDER)
    # Прочий 4xx — «Ошибка провайдера».
    return KeyCheckResult("error", REASON_PROVIDER)


async def check_key(provider: AiProvider, api_key: str) -> KeyCheckResult:
    """Проверяет валидность ключа у провайдера (`GET /v1/models`).

    200 → `working`; 401/403/429/прочий 4xx → `error` с русской причиной;
    таймаут/сеть/5xx → `unknown` (после ограниченных ретраев). Ключ не логируется.
    """
    settings = get_settings()
    url, headers = _build_request(provider, api_key)
    max_attempts = len(_BACKOFF_DELAYS_SEC) + 1

    async with httpx.AsyncClient(timeout=settings.ai_provider_timeout_sec, verify=True) as client:
        for attempt in range(max_attempts):
            try:
                response = await client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError):
                # Транзиентная сетевая ошибка — ретрай, затем unknown (без логов с ключом).
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return KeyCheckResult("unknown", None)
            except httpx.HTTPError:
                # Прочая ошибка httpx — неретраябельна, трактуем как транзиентную.
                return KeyCheckResult("unknown", None)

            status_code = response.status_code
            if status_code == httpx.codes.OK:  # 200
                return KeyCheckResult("working", None)
            if 500 <= status_code < 600:
                # 5xx провайдера — транзиентно: ретрай, затем unknown.
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return KeyCheckResult("unknown", None)
            # 4xx — детерминированный исход авторизации/квоты.
            return _map_client_error(status_code, response)

    # Недостижимо: цикл возвращает результат в каждой ветке.
    return KeyCheckResult("unknown", None)


async def probe_credits(provider: AiProvider, api_key: str) -> CreditProbeResult:
    """Минимальный платный inference для детекта кредитов (ADR-075).

    200 → `ok`; billing/quota → `depleted`; таймаут/5xx/rate-limit/auth → `unknown`
    (credit_status в БД не флипается — auth ловит health-монитор).
    """
    settings = get_settings()
    max_attempts = len(_BACKOFF_DELAYS_SEC) + 1

    if provider is AiProvider.openai:
        url = f"{settings.openai_api_base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
        payload: dict[str, Any] = {
            "model": settings.ai_key_credit_probe_openai_model,
            "messages": [{"role": "user", "content": _CREDIT_PROMPT}],
            "max_tokens": 1,
        }
    else:
        url = f"{settings.anthropic_api_base.rstrip('/')}/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": settings.anthropic_api_version,
            "content-type": "application/json",
        }
        payload = {
            "model": settings.ai_key_credit_probe_anthropic_model,
            "messages": [{"role": "user", "content": _CREDIT_PROMPT}],
            "max_tokens": 1,
        }

    async with httpx.AsyncClient(timeout=settings.ai_provider_timeout_sec, verify=True) as client:
        for attempt in range(max_attempts):
            try:
                response = await client.post(url, headers=headers, json=payload)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return CreditProbeResult("unknown", None)
            except httpx.HTTPError:
                return CreditProbeResult("unknown", None)

            status_code = response.status_code
            if status_code == httpx.codes.OK:
                return CreditProbeResult("ok", None)
            if 500 <= status_code < 600:
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return CreditProbeResult("unknown", None)

            body = _parse_body(response)
            if _is_credit_depleted(provider, status_code, body):
                return CreditProbeResult("depleted", REASON_QUOTA)
            # 401/403/429 без quota / прочий 4xx — не флипаем credit_status.
            return CreditProbeResult("unknown", None)

    return CreditProbeResult("unknown", None)


__all__ = [
    "REASON_FORBIDDEN",
    "REASON_INVALID",
    "REASON_PROVIDER",
    "REASON_QUOTA",
    "CheckOutcome",
    "CreditOutcome",
    "CreditProbeResult",
    "KeyCheckResult",
    "check_key",
    "probe_credits",
]
