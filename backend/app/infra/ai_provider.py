"""Проверка валидности AI-ключа у провайдера через read-only `GET /v1/models`.

Проверяется ТОЛЬКО валидность/блокировка ключа — токены не тратятся
(modules/ai-keys#проверка-ключа-у-провайдера-нормативно, ADR-010). Транзиентные
ошибки (таймаут/сеть/5xx) → исход `unknown` (статус не меняется, алерт не шлётся).
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

# Русскоязычные причины ошибки (записываются в error_message, modules/ai-keys).
REASON_INVALID = "Ключ недействителен"
REASON_FORBIDDEN = "Доступ запрещён"
REASON_QUOTA = "Недостаточно средств"
REASON_PROVIDER = "Ошибка провайдера"

# Backoff между попытками на транзиентных ошибках; попыток = len + 1 (т.е. 3).
_BACKOFF_DELAYS_SEC = (0.2, 0.5)


@dataclass(frozen=True)
class KeyCheckResult:
    """Чистый результат проверки: исход + причина (только при `error`)."""

    outcome: CheckOutcome
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


__all__ = [
    "REASON_FORBIDDEN",
    "REASON_INVALID",
    "REASON_PROVIDER",
    "REASON_QUOTA",
    "CheckOutcome",
    "KeyCheckResult",
    "check_key",
]
