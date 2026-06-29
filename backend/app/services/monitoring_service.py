"""Маппинг метрик из Prometheus в схему карточки (modules/monitoring, 02-promql.md).

Все PromQL — строго из 02-promql.md. Конвертация единиц и зоны — оттуда же и из
04-api.md. Деградация: для списка — metrics=null; для одиночного endpoint — 502.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.thresholds import usage_to_zone
from app.infra.prometheus import PrometheusClient, PrometheusUnavailable
from app.schemas.metrics import Metric, MetricDetail, ServerMetrics


@dataclass(frozen=True)
class InstanceMetrics:
    """Метрики одного instance после маппинга."""

    online: bool
    uptime_seconds: int | None
    last_updated: datetime | None
    metrics: ServerMetrics | None


def _instance_matcher(instances: list[str]) -> str:
    """Label-селектор instance: точный для одного, regex для нескольких."""
    if len(instances) == 1:
        return f'instance="{instances[0]}"'
    pattern = "|".join(re.escape(inst) for inst in instances)
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


class MonitoringService:
    """Выполняет PromQL и маппит результат в схему метрик карточки."""

    def __init__(self, client: PrometheusClient) -> None:
        self._client = client

    async def fetch_for_instances(self, instances: list[str]) -> dict[str, InstanceMetrics]:
        """Батч-запрос метрик для набора instance.

        Бросает PrometheusUnavailable, если Prometheus недоступен (решение о
        graceful degradation принимает вызывающий слой).
        """
        if not instances:
            return {}

        queries = _build_queries(_instance_matcher(instances))
        keys = list(queries.keys())
        results = await asyncio.gather(*(self._client.query(queries[k]) for k in keys))
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
