"""Unit-тесты max-over-window / min-over-window режима MonitoringService (ADR-016/018).

Покрывают: генерацию PromQL нотификатора (usage CPU/RAM/SSD оборачивается в
max_over_time((<expr>)[Ws:15s]), `up` — в min_over_time(up[Ws]) без subquery,
а uptime/detail остаются мгновенными, ADR-018); регрессию read-path (window_sec=None →
прежние инстант-запросы без max_over_time/min_over_time); windowed offline-детект
(min_over_time(up[W]) == 0 → offline) при неизменном мгновенном up (up_value == 1.0);
раздельность TTL-кэша по window_sec (мгновенный UI-результат и windowed-результат
нотификатора не смешиваются даже при совпадении набора instances). Prometheus —
стаб (без сети); нормативный источник — 02-promql.md#notifier-max-over-window, ADR-016/018.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.services import monitoring_service as monitoring_module
from app.services.monitoring_service import (
    MonitoringService,
    _build_queries,
    _instance_matcher,
)

_INST = "10.0.0.7:9100"
_WINDOW_SEC = 90
_STEP = "15s"
# usage-метрики оборачиваются max_over_time; `up` — min_over_time (ADR-018);
# остальные ключи — мгновенные.
_USAGE_KEYS = ("cpu_usage", "ram_usage", "ssd_usage")
_INSTANT_KEYS = (
    "uptime",
    "cpu_cores",
    "ram_total",
    "ram_used",
    "ssd_total",
    "ssd_used",
)
_GIB = 1024**3


@pytest.fixture(autouse=True)
def clear_monitoring_cache() -> None:
    monitoring_module._cache.clear()
    monitoring_module._inflight.clear()


def test_build_queries_wraps_only_usage_in_max_over_time_windowed() -> None:
    matcher = _instance_matcher([_INST])
    instant = _build_queries(matcher)
    windowed = _build_queries(matcher, window_sec=_WINDOW_SEC)

    # usage CPU/RAM/SSD: max_over_time за окно вокруг того же инстант-выражения.
    for key in _USAGE_KEYS:
        assert windowed[key] == f"max_over_time(({instant[key]})[{_WINDOW_SEC}s:{_STEP}])"

    # up: min_over_time за окно (ADR-018) — прямой range-vector `up[Ws]` без subquery/step.
    assert windowed["up"] == f"min_over_time({instant['up']}[{_WINDOW_SEC}s])"
    assert "min_over_time" not in instant["up"]
    assert "max_over_time" not in windowed["up"]  # up не оборачивается в max

    # uptime/detail (cores/GB) — мгновенные, не оборачиваются.
    for key in _INSTANT_KEYS:
        assert windowed[key] == instant[key]
        assert "max_over_time" not in windowed[key]
        assert "min_over_time" not in windowed[key]


def test_build_queries_instant_default_has_no_max_over_time_read_path_regression() -> None:
    # window_sec=None (default, UI/read-path) → прежние инстант-запросы без обёртки.
    queries = _build_queries(_instance_matcher([_INST]))

    assert queries == _build_queries(_instance_matcher([_INST]), window_sec=None)
    for promql in queries.values():
        assert "max_over_time" not in promql
        assert "min_over_time" not in promql  # up тоже мгновенный в read-path (ADR-018)


def _vector(instance: str, value: float, timestamp: float = 1000.0) -> dict[str, object]:
    return {"metric": {"instance": instance}, "value": [timestamp, str(value)]}


class _RoutedPrometheus:
    """Стаб Prometheus: возвращает ответ по точному тексту PromQL-запроса.

    Карта строится из _build_queries для обоих режимов, поэтому фейк валидирует,
    что сервис шлёт ровно ожидаемый (windowed/instant) PromQL. Порядок вычисления
    usage NaN не возникает — все значения численные.
    """

    def __init__(self, responses: dict[str, list[dict[str, object]]]) -> None:
        self._responses = responses
        self.seen: list[str] = []

    async def query(self, promql: str) -> list[dict[str, Any]]:
        self.seen.append(promql)
        return self._responses.get(promql, [])


def _responses_for(
    matcher_inst: str, *, window_sec: int | None, cpu_usage: float
) -> dict[str, list[dict[str, object]]]:
    matcher = _instance_matcher([matcher_inst])
    q = _build_queries(matcher, window_sec=window_sec)
    return {
        q["up"]: [_vector(matcher_inst, 1)],
        q["uptime"]: [_vector(matcher_inst, 3600)],
        q["cpu_usage"]: [_vector(matcher_inst, cpu_usage)],
        q["cpu_cores"]: [_vector(matcher_inst, 8)],
        q["ram_usage"]: [_vector(matcher_inst, 20.0)],
        q["ram_total"]: [_vector(matcher_inst, 16 * _GIB)],
        q["ram_used"]: [_vector(matcher_inst, 4 * _GIB)],
        q["ssd_usage"]: [_vector(matcher_inst, 30.0)],
        q["ssd_total"]: [_vector(matcher_inst, 500 * _GIB)],
        q["ssd_used"]: [_vector(matcher_inst, 100 * _GIB)],
    }


@pytest.mark.asyncio
async def test_fetch_windowed_issues_max_over_time_and_maps_zone_from_max() -> None:
    # Windowed CPU usage = 95 (max-за-окно, red). detail/online — мгновенные.
    prom = _RoutedPrometheus(_responses_for(_INST, window_sec=_WINDOW_SEC, cpu_usage=95.0))
    service = MonitoringService(prom)  # type: ignore[arg-type]

    result = await service.fetch_for_instances([_INST], window_sec=_WINDOW_SEC)

    im = result[_INST]
    assert im.online is True
    assert im.metrics is not None
    # Зона выведена из максимума за окно через usage_to_zone (red > 90).
    assert im.metrics.cpu.usage_percent == 95.0
    assert im.metrics.cpu.zone == "red"
    # detail остаётся мгновенным (cores/GB не оборачиваются).
    assert im.metrics.cpu.detail.total == 8
    assert im.metrics.ram.detail.model_dump() == {"value": 4.0, "total": 16.0, "unit": "GB"}
    # Сервис реально послал windowed PromQL для usage-метрик.
    assert any(promql.startswith("max_over_time(") for promql in prom.seen)


@pytest.mark.asyncio
async def test_cache_key_separates_instant_and_windowed_results() -> None:
    # Один и тот же instance: мгновенный (UI) usage green, windowed (notifier) red.
    # Ключ кэша включает window_sec → результаты не смешиваются (ADR-016).
    responses = {
        **_responses_for(_INST, window_sec=None, cpu_usage=10.0),
        **_responses_for(_INST, window_sec=_WINDOW_SEC, cpu_usage=95.0),
    }
    prom = _RoutedPrometheus(responses)
    service = MonitoringService(prom)  # type: ignore[arg-type]

    instant = await service.fetch_for_instances([_INST])
    windowed = await service.fetch_for_instances([_INST], window_sec=_WINDOW_SEC)

    instant_metrics = instant[_INST].metrics
    windowed_metrics = windowed[_INST].metrics
    assert instant_metrics is not None
    assert windowed_metrics is not None
    # Мгновенный green vs windowed red — записи кэша раздельны, не переиспользованы.
    assert instant_metrics.cpu.zone == "green"
    assert instant_metrics.cpu.usage_percent == 10.0
    assert windowed_metrics.cpu.zone == "red"
    assert windowed_metrics.cpu.usage_percent == 95.0
    # Два раздельных ключа кэша (instant + windowed), не один.
    assert len(monitoring_module._cache) == 2


@pytest.mark.asyncio
async def test_fetch_windowed_offline_when_up_min_zero_in_window() -> None:
    # ADR-018: online = min_over_time(up[W]) == 1. Провал up в любой точке окна →
    # min=0 → offline (metrics=None). Сервис шлёт именно min_over_time(up[...]).
    matcher = _instance_matcher([_INST])
    q = _build_queries(matcher, window_sec=_WINDOW_SEC)
    responses = _responses_for(_INST, window_sec=_WINDOW_SEC, cpu_usage=95.0)
    responses[q["up"]] = [_vector(_INST, 0)]  # up падал в окне → min_over_time == 0
    prom = _RoutedPrometheus(responses)
    service = MonitoringService(prom)  # type: ignore[arg-type]

    result = await service.fetch_for_instances([_INST], window_sec=_WINDOW_SEC)

    im = result[_INST]
    assert im.online is False
    assert im.metrics is None
    assert im.uptime_seconds is None
    # Windowed offline-детект послал min_over_time(up[...]), не мгновенный up.
    assert any(promql.startswith("min_over_time(up") for promql in prom.seen)


@pytest.mark.asyncio
async def test_fetch_windowed_online_when_up_min_one_whole_window() -> None:
    # min_over_time(up[W]) == 1 (up был 1 всё окно) → online (ADR-018).
    prom = _RoutedPrometheus(_responses_for(_INST, window_sec=_WINDOW_SEC, cpu_usage=10.0))
    service = MonitoringService(prom)  # type: ignore[arg-type]

    result = await service.fetch_for_instances([_INST], window_sec=_WINDOW_SEC)

    assert result[_INST].online is True
    assert any(promql.startswith("min_over_time(up") for promql in prom.seen)


@pytest.mark.asyncio
async def test_fetch_instant_online_up_value_unchanged_no_min_over_time() -> None:
    # Регрессия read-path: window_sec=None → up остаётся мгновенным (up_value == 1.0 →
    # online), min_over_time НЕ применяется (ADR-018 не трогает UI-путь).
    prom = _RoutedPrometheus(_responses_for(_INST, window_sec=None, cpu_usage=10.0))
    service = MonitoringService(prom)  # type: ignore[arg-type]

    result = await service.fetch_for_instances([_INST])

    assert result[_INST].online is True
    assert all(not promql.startswith("min_over_time") for promql in prom.seen)
