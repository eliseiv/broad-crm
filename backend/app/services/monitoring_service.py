"""Маппинг метрик из Prometheus в схему карточки (modules/monitoring, 02-promql.md).

Все PromQL — строго из 02-promql.md. Конвертация единиц и зоны — оттуда же и из
04-api.md. Деградация: для списка — metrics=null; для одиночного endpoint — 502.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.domain.thresholds import usage_to_zone
from app.infra.prometheus import PrometheusClient, PrometheusUnavailable
from app.logging import get_logger
from app.schemas.metrics import Metric, MetricDetail, ServerMetrics

logger = get_logger(__name__)

# Максимум одновременных PromQL-запросов ко всему Prometheus (защита от
# превышения query.max-concurrency при наложении опросов). Глобальный на процесс.
_MAX_CONCURRENT_QUERIES = 4
_PROM_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_QUERIES)


@dataclass(frozen=True)
class InstanceMetrics:
    """Метрики одного instance после маппинга."""

    online: bool
    uptime_seconds: int | None
    last_updated: datetime | None
    metrics: ServerMetrics | None


def _instance_matcher(instances: list[str]) -> str:
    """Label-селектор instance: точный для одного, regex (=~) для нескольких.

    Для regex каждый instance экранируется re.escape (RE2: точки → `\\.`), затем
    одиночный бэкслэш удваивается, чтобы пережить парсер double-quoted строк
    PromQL: источник `\\\\.` → строка `\\.` → RE2 получает корректный `\\.`.
    Без удвоения PromQL падает с `parse error: unknown escape sequence`.
    """
    if len(instances) == 1:
        return f'instance="{instances[0]}"'
    pattern = "|".join(re.escape(inst).replace("\\", "\\\\") for inst in instances)
    return f'instance=~"{pattern}"'


def _build_queries(matcher: str) -> dict[str, str]:
    """Полный набор PromQL-запросов (02-promql.md) с подставленным селектором."""
    ssd_sel = f'{matcher},mountpoint="/",fstype!~"tmpfs|overlay"'
    return {
        "up": f"up{{{matcher}}}",
        "uptime": (f"node_time_seconds{{{matcher}}} - node_boot_time_seconds{{{matcher}}}"),
        "cpu_usage": (
            f"100 - (avg by(instance)"
            f'(rate(node_cpu_seconds_total{{{matcher},mode="idle"}}[1m])) * 100)'
        ),
        "cpu_cores": (f'count by(instance)(node_cpu_seconds_total{{{matcher},mode="idle"}})'),
        "ram_usage": (
            f"(1 - node_memory_MemAvailable_bytes{{{matcher}}}"
            f" / node_memory_MemTotal_bytes{{{matcher}}}) * 100"
        ),
        "ram_total": f"node_memory_MemTotal_bytes{{{matcher}}}",
        "ram_used": (
            f"node_memory_MemTotal_bytes{{{matcher}}}"
            f" - node_memory_MemAvailable_bytes{{{matcher}}}"
        ),
        "ssd_usage": (
            f"(1 - node_filesystem_avail_bytes{{{ssd_sel}}}"
            f" / node_filesystem_size_bytes{{{ssd_sel}}}) * 100"
        ),
        "ssd_total": f"node_filesystem_size_bytes{{{ssd_sel}}}",
        "ssd_used": (
            f"node_filesystem_size_bytes{{{ssd_sel}}}"
            f" - node_filesystem_avail_bytes{{{ssd_sel}}}"
        ),
    }


def _parse_values(result: list[dict[str, Any]]) -> dict[str, float]:
    """Vector-результат → {instance: value} (пропускает NaN и некорректные)."""
    parsed: dict[str, float] = {}
    for item in result:
        instance = item.get("metric", {}).get("instance")
        value = item.get("value")
        if not instance or not isinstance(value, list) or len(value) != 2:
            continue
        try:
            num = float(value[1])
        except (TypeError, ValueError):
            continue
        if num != num:  # NaN
            continue
        parsed[instance] = num
    return parsed


def _parse_timestamps(result: list[dict[str, Any]]) -> dict[str, float]:
    """Vector-результат → {instance: eval_timestamp}."""
    parsed: dict[str, float] = {}
    for item in result:
        instance = item.get("metric", {}).get("instance")
        value = item.get("value")
        if not instance or not isinstance(value, list) or len(value) != 2:
            continue
        try:
            parsed[instance] = float(value[0])
        except (TypeError, ValueError):
            continue
    return parsed


def _clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, round(value, 1)))


def _bytes_to_gb(value: float | None) -> float | None:
    return None if value is None else round(value / 1024**3, 1)


def _cpu_detail(
    instance: str,
    cores: dict[str, float],
) -> MetricDetail:
    """CPU detail — всегда cores (02-promql.md, Q-MON-1, TD-013): unit "cores",
    value null, total = число логических ядер; если серия недоступна → total null."""
    if instance in cores:
        return MetricDetail(value=None, total=round(cores[instance]), unit="cores")
    return MetricDetail(value=None, total=None, unit="cores")


def _build_metrics(maps: dict[str, dict[str, float]], instance: str) -> ServerMetrics | None:
    """Собирает ServerMetrics для instance; None, если нет usage хотя бы одной метрики."""
    cpu_usage = maps["cpu_usage"].get(instance)
    ram_usage = maps["ram_usage"].get(instance)
    ssd_usage = maps["ssd_usage"].get(instance)
    if cpu_usage is None or ram_usage is None or ssd_usage is None:
        return None

    cpu_pct = _clamp_pct(cpu_usage)
    ram_pct = _clamp_pct(ram_usage)
    ssd_pct = _clamp_pct(ssd_usage)

    cpu = Metric(
        usage_percent=cpu_pct,
        zone=usage_to_zone(cpu_pct),
        detail=_cpu_detail(instance, maps["cpu_cores"]),
    )
    ram = Metric(
        usage_percent=ram_pct,
        zone=usage_to_zone(ram_pct),
        detail=MetricDetail(
            value=_bytes_to_gb(maps["ram_used"].get(instance)),
            total=_bytes_to_gb(maps["ram_total"].get(instance)),
            unit="GB",
        ),
    )
    ssd = Metric(
        usage_percent=ssd_pct,
        zone=usage_to_zone(ssd_pct),
        detail=MetricDetail(
            value=_bytes_to_gb(maps["ssd_used"].get(instance)),
            total=_bytes_to_gb(maps["ssd_total"].get(instance)),
            unit="GB",
        ),
    )
    return ServerMetrics(cpu=cpu, ram=ram, ssd=ssd)


# --- Short-lived TTL-кэш + single-flight для read-path (общие на процесс) ---
# Ключ = tuple(sorted(instances)). Кэшируется только успешный результат.
_CacheKey = tuple[str, ...]
_cache: dict[_CacheKey, tuple[float, dict[str, InstanceMetrics]]] = {}
_inflight: dict[_CacheKey, asyncio.Future[dict[str, InstanceMetrics]]] = {}
_state_lock = asyncio.Lock()


def _cache_get(key: _CacheKey, ttl: float) -> dict[str, InstanceMetrics] | None:
    """Возвращает свежий (в пределах TTL) кэш или None. Без await — атомарно."""
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at <= ttl:
        return value
    return None


class MonitoringService:
    """Выполняет PromQL и маппит результат в схему метрик карточки."""

    def __init__(self, client: PrometheusClient) -> None:
        self._client = client

    async def fetch_for_instances(self, instances: list[str]) -> dict[str, InstanceMetrics]:
        """Батч-запрос метрик для набора instance с TTL-кэшем и single-flight.

        Свежий кэш возвращается без запросов к Prometheus. При промахе
        одновременные вызовы с тем же ключом ждут один общий запрос (single-flight).
        Бросает PrometheusUnavailable, если Prometheus недоступен (решение о
        graceful degradation принимает вызывающий слой); ошибки не кэшируются.
        """
        if not instances:
            return {}

        key: _CacheKey = tuple(sorted(instances))
        ttl = get_settings().metrics_cache_ttl_sec

        cached = _cache_get(key, ttl)
        if cached is not None:
            return cached

        return await self._fetch_single_flight(key, instances, ttl)

    async def _fetch_single_flight(
        self, key: _CacheKey, instances: list[str], ttl: float
    ) -> dict[str, InstanceMetrics]:
        """Single-flight: один общий запрос на ключ, остальные ждут его результат."""
        async with _state_lock:
            cached = _cache_get(key, ttl)
            if cached is not None:
                return cached
            existing = _inflight.get(key)
            if existing is not None:
                future = existing
                is_owner = False
            else:
                future = asyncio.get_running_loop().create_future()
                _inflight[key] = future
                is_owner = True

        if not is_owner:
            return await future

        try:
            result = await self._execute_batch(instances)
        except BaseException as exc:
            async with _state_lock:
                _inflight.pop(key, None)
            if not future.done():
                future.set_exception(exc)
            raise
        else:
            async with _state_lock:
                _cache[key] = (time.monotonic(), result)
                _inflight.pop(key, None)
            if not future.done():
                future.set_result(result)
            return result

    async def _guarded_query(self, promql: str) -> list[dict[str, Any]]:
        """Один PromQL-запрос под глобальным семафором конкурентности."""
        async with _PROM_SEMAPHORE:
            return await self._client.query(promql)

    async def _execute_batch(self, instances: list[str]) -> dict[str, InstanceMetrics]:
        """Выполняет батч PromQL (с ограничением конкурентности) и маппит в схему."""
        queries = _build_queries(_instance_matcher(instances))
        keys = list(queries.keys())
        results = await asyncio.gather(*(self._guarded_query(queries[k]) for k in keys))
        raw = dict(zip(keys, results, strict=True))

        maps = {k: _parse_values(raw[k]) for k in keys}
        up_ts = _parse_timestamps(raw["up"])

        output: dict[str, InstanceMetrics] = {}
        for instance in instances:
            up_value = maps["up"].get(instance)
            online = up_value == 1.0
            if not online:
                output[instance] = InstanceMetrics(
                    online=False, uptime_seconds=None, last_updated=None, metrics=None
                )
                continue

            metrics = _build_metrics(maps, instance)
            uptime_raw = maps["uptime"].get(instance)
            uptime_seconds = int(uptime_raw) if uptime_raw is not None else None
            ts = up_ts.get(instance)
            last_updated = datetime.fromtimestamp(ts, tz=UTC) if ts is not None else None
            output[instance] = InstanceMetrics(
                online=True,
                uptime_seconds=uptime_seconds,
                last_updated=last_updated,
                metrics=metrics,
            )
        return output

    async def fetch_one(self, instance: str) -> InstanceMetrics:
        """Метрики одного instance; PrometheusUnavailable пробрасывается (→ 502)."""
        result = await self.fetch_for_instances([instance])
        return result.get(
            instance,
            InstanceMetrics(online=False, uptime_seconds=None, last_updated=None, metrics=None),
        )


__all__ = ["InstanceMetrics", "MonitoringService", "PrometheusUnavailable"]
