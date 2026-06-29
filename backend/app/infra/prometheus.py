"""Низкоуровневый async-клиент Prometheus HTTP API (httpx).

Выполняет instant-запросы к `${PROMETHEUS_URL}/api/v1/query`. TLS verify включён
по умолчанию. Недоступность Prometheus сигнализируется PrometheusUnavailable.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)


class PrometheusUnavailable(Exception):
    """Prometheus недоступен или вернул ошибку/таймаут."""


class PrometheusClient:
    """Тонкая обёртка над Prometheus instant query API."""

    def __init__(self, base_url: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    async def query(self, promql: str) -> list[dict[str, Any]]:
        """Выполняет instant-запрос, возвращает массив `result` (vector).

        Бросает PrometheusUnavailable при сетевой ошибке/таймауте/некорректном ответе.
        """
        url = f"{self._base_url}/api/v1/query"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=True) as client:
                response = await client.get(url, params={"query": promql})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("prometheus_query_failed", error_type=type(exc).__name__)
            raise PrometheusUnavailable(str(exc)) from exc

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
