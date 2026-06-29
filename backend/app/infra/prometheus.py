"""Низкоуровневый async-клиент Prometheus HTTP API (httpx).

Выполняет instant-запросы к `${PROMETHEUS_URL}/api/v1/query`. TLS verify включён
по умолчанию. Недоступность Prometheus сигнализируется PrometheusUnavailable.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)

# Транзиентные HTTP-статусы Prometheus, при которых имеет смысл ретрай.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Задержки backoff между попытками; число попыток = len + 1 (т.е. 3).
_BACKOFF_DELAYS_SEC = (0.2, 0.5)


class PrometheusUnavailable(Exception):
    """Prometheus недоступен или вернул ошибку/таймаут."""


class PrometheusClient:
    """Тонкая обёртка над Prometheus instant query API."""

    def __init__(self, base_url: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    async def query(self, promql: str) -> list[dict[str, Any]]:
        """Выполняет instant-запрос, возвращает массив `result` (vector).

        До 3 попыток с backoff на транзиентных ошибках (таймаут/сеть/HTTP
        429/5xx). Неретраябельные ошибки (4xx кроме 429, некорректный JSON,
        status != success) → сразу PrometheusUnavailable.
        """
        url = f"{self._base_url}/api/v1/query"
        max_attempts = len(_BACKOFF_DELAYS_SEC) + 1
        # Один клиент на вызов query, переиспользуется между попытками.
        async with httpx.AsyncClient(timeout=self._timeout, verify=True) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.get(url, params={"query": promql})
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code in _RETRYABLE_STATUS and attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "prometheus_query_failed",
                        status=status_code,
                        attempt=attempt + 1,
                    )
                    raise PrometheusUnavailable(str(exc)) from exc
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "prometheus_query_failed",
                        error_type=type(exc).__name__,
                        attempt=attempt + 1,
                    )
                    raise PrometheusUnavailable(str(exc)) from exc
                except (httpx.HTTPError, ValueError) as exc:
                    # Прочие ошибки httpx / парсинга JSON — неретраябельны.
                    logger.warning("prometheus_query_failed", error_type=type(exc).__name__)
                    raise PrometheusUnavailable(str(exc)) from exc

                return self._extract_vector(payload)

        # Недостижимо: цикл либо возвращает результат, либо бросает исключение.
        raise PrometheusUnavailable("Prometheus query failed after retries")

    @staticmethod
    def _extract_vector(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Извлекает vector-результат из ответа Prometheus."""
        if payload.get("status") != "success":
            logger.warning("prometheus_query_status_error", status=payload.get("status"))
            raise PrometheusUnavailable("Prometheus вернул status != success")

        data = payload.get("data", {})
        if data.get("resultType") != "vector":
            return []
        result = data.get("result", [])
        return result if isinstance(result, list) else []


def get_prometheus_client() -> PrometheusClient:
    """Фабрика клиента Prometheus из настроек."""
    settings = get_settings()
    return PrometheusClient(
        base_url=settings.prometheus_url,
        timeout_sec=settings.prom_query_timeout_sec,
    )
