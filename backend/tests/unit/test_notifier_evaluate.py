"""Unit-тесты чистой функции перехода state-машины нотификатора (modules/notifier).

Покрывают матрицу эскалаций/деэскалаций, offline-переходы, alert-on-first-elevated
(ADR-014: отсутствие базы ≡ здоровый baseline online+green×3), возврат offline→online,
online без метрик (zone_* → NULL), смешанную эскалацию за один опрос, дедуп по зоне и
персист деэскалации с повторным ростом. Без сети/БД — нормативный источник:
modules/notifier «State-машина», ADR-014.
"""

from __future__ import annotations

from app.domain.thresholds import Zone
from app.schemas.metrics import Metric, MetricDetail, ServerMetrics
from app.services.monitoring_service import InstanceMetrics
from app.services.notifier_service import ServerAlertState, evaluate

NAME = "srv"
IP = "10.0.0.1"

_CORES = MetricDetail(value=None, total=8, unit="cores")
_GB = MetricDetail(value=1.0, total=2.0, unit="GB")

# Репрезентативные проценты по зонам (для текста сообщений).
_PCT: dict[Zone, float] = {"green": 10.0, "yellow": 85.0, "red": 95.0}


def _metric(zone: Zone, *, unit: str = "cores") -> Metric:
    detail = _CORES if unit == "cores" else _GB
    return Metric(usage_percent=_PCT[zone], zone=zone, detail=detail)


def _online(cpu: Zone = "green", ram: Zone = "green", ssd: Zone = "green") -> InstanceMetrics:
    metrics = ServerMetrics(
        cpu=_metric(cpu),
        ram=_metric(ram, unit="GB"),
        ssd=_metric(ssd, unit="GB"),
    )
    return InstanceMetrics(online=True, uptime_seconds=100, last_updated=None, metrics=metrics)


def _online_no_metrics() -> InstanceMetrics:
    return InstanceMetrics(online=True, uptime_seconds=100, last_updated=None, metrics=None)


def _offline() -> InstanceMetrics:
    return InstanceMetrics(online=False, uptime_seconds=None, last_updated=None, metrics=None)


def _state(online: bool, zones: dict[str, Zone] | None) -> ServerAlertState:
    return ServerAlertState(online=online, zones=zones)


_GREEN = {"cpu": "green", "ram": "green", "ssd": "green"}


# -------------------------------------------- первая встреча (alert-on-first-elevated)
# ADR-014: prev is None ≡ здоровый baseline (online, green×3). Впервые увиденный уже
# в повышенной зоне/offline → ровно один catch-up-алерт; green/online — молча.
def test_first_seen_online_green_no_alert_base_fixed() -> None:
    state, alerts = evaluate(None, _online("green", "green", "green"), name=NAME, ip=IP)
    assert alerts == []
    assert state == _state(True, {"cpu": "green", "ram": "green", "ssd": "green"})


def test_first_seen_online_yellow_alerts_once_warning() -> None:
    # baseline green → yellow ⇒ ровно один warning (alert-on-first-elevated).
    state, alerts = evaluate(None, _online("yellow"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["warning"]
    assert "🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡" in alerts[0].text
    assert "CPU: Нагрузка более 85%" in alerts[0].text
    assert state.zones == {"cpu": "yellow", "ram": "green", "ssd": "green"}


def test_first_seen_online_red_alerts_once_critical() -> None:
    # baseline green → red ⇒ ровно один critical (по всем трём red-метрикам).
    state, alerts = evaluate(None, _online("red", "red", "red"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["critical"]
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in alerts[0].text
    assert state.zones == {"cpu": "red", "ram": "red", "ssd": "red"}


def test_first_seen_offline_alerts_once_offline() -> None:
    # baseline online → offline ⇒ ровно один offline-алерт (ADR-014 offline-first).
    state, alerts = evaluate(None, _offline(), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["offline"]
    assert "Сервер не доступен" in alerts[0].text
    assert state == _state(False, None)


# ------------------------------------------------------------------- эскалации
def test_green_to_yellow_one_warning() -> None:
    prev = _state(True, dict(_GREEN))
    state, alerts = evaluate(prev, _online(cpu="yellow"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["warning"]
    assert "🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡" in alerts[0].text
    assert "CPU: Нагрузка более 85%" in alerts[0].text
    assert state.zones == {"cpu": "yellow", "ram": "green", "ssd": "green"}


def test_yellow_to_yellow_silent() -> None:
    prev = _state(True, {"cpu": "yellow", "ram": "green", "ssd": "green"})
    state, alerts = evaluate(prev, _online(cpu="yellow"), name=NAME, ip=IP)
    assert alerts == []
    assert state.zones == {"cpu": "yellow", "ram": "green", "ssd": "green"}


def test_yellow_to_red_critical() -> None:
    prev = _state(True, {"cpu": "yellow", "ram": "green", "ssd": "green"})
    state, alerts = evaluate(prev, _online(cpu="red"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["critical"]
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in alerts[0].text
    assert "CPU: Нагрузка более 95%" in alerts[0].text


def test_green_to_red_direct_critical() -> None:
    prev = _state(True, dict(_GREEN))
    state, alerts = evaluate(prev, _online(cpu="red"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["critical"]


# ----------------------------------------------------------------- деэскалация
def test_red_to_yellow_silent_state_updates() -> None:
    prev = _state(True, {"cpu": "red", "ram": "green", "ssd": "green"})
    state, alerts = evaluate(prev, _online(cpu="yellow"), name=NAME, ip=IP)
    assert alerts == []
    assert state.zones == {"cpu": "yellow", "ram": "green", "ssd": "green"}


def test_red_to_green_silent_state_updates() -> None:
    prev = _state(True, {"cpu": "red", "ram": "red", "ssd": "red"})
    state, alerts = evaluate(prev, _online("green", "green", "green"), name=NAME, ip=IP)
    assert alerts == []
    assert state.zones == dict(_GREEN)


def test_yellow_to_green_silent_state_updates() -> None:
    prev = _state(True, {"cpu": "yellow", "ram": "yellow", "ssd": "green"})
    state, alerts = evaluate(prev, _online("green", "green", "green"), name=NAME, ip=IP)
    assert alerts == []
    assert state.zones == dict(_GREEN)


# ------------------------------------------------------------------- offline
def test_online_to_offline_one_offline_message_zones_reset() -> None:
    prev = _state(True, {"cpu": "red", "ram": "green", "ssd": "green"})
    state, alerts = evaluate(prev, _offline(), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["offline"]
    assert "Сервер не доступен" in alerts[0].text
    assert state == _state(False, None)


def test_offline_to_offline_silent() -> None:
    prev = _state(False, None)
    state, alerts = evaluate(prev, _offline(), name=NAME, ip=IP)
    assert alerts == []
    assert state == _state(False, None)


def test_offline_to_online_under_load_realerts_base_green() -> None:
    # Возврат: база = green по всем → red-метрики снова алертятся.
    prev = _state(False, None)
    state, alerts = evaluate(prev, _online(cpu="red", ram="yellow"), name=NAME, ip=IP)
    kinds = sorted(a.kind for a in alerts)
    assert kinds == ["critical", "warning"]
    assert state.zones == {"cpu": "red", "ram": "yellow", "ssd": "green"}


# ------------------------------------------------------- online без метрик
def test_online_no_metrics_zones_reset_null_online_true_no_alert() -> None:
    # ADR-014: online + metrics=None ⇒ зоны не оцениваются, zone_* → NULL (zones=None),
    # online=True, алертов нет. Известный minor — задокументированное поведение.
    prev = _state(True, {"cpu": "yellow", "ram": "green", "ssd": "green"})
    state, alerts = evaluate(prev, _online_no_metrics(), name=NAME, ip=IP)
    assert alerts == []
    assert state == _state(True, None)


def test_first_seen_online_no_metrics_zones_none() -> None:
    state, alerts = evaluate(None, _online_no_metrics(), name=NAME, ip=IP)
    assert alerts == []
    assert state == _state(True, None)


def test_none_base_treated_as_green_realerts_on_load() -> None:
    # prev online но zones=None (был online без метрик) → метрики появились под
    # нагрузкой → None-база ≡ green → переалерт.
    prev = _state(True, None)
    state, alerts = evaluate(prev, _online(cpu="red"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["critical"]
    assert state.zones == {"cpu": "red", "ram": "green", "ssd": "green"}


# ------------------------------------------------ смешанная эскалация за опрос
def test_mixed_escalation_two_messages_warning_and_critical() -> None:
    prev = _state(True, dict(_GREEN))
    state, alerts = evaluate(prev, _online(cpu="yellow", ram="red"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["warning", "critical"]
    warning = next(a for a in alerts if a.kind == "warning")
    critical = next(a for a in alerts if a.kind == "critical")
    assert "CPU: Нагрузка более 85%" in warning.text
    assert "RAM:" not in warning.text
    assert "RAM: Нагрузка более 95%" in critical.text
    assert "CPU:" not in critical.text
    assert state.zones == {"cpu": "yellow", "ram": "red", "ssd": "green"}


def test_partial_escalation_only_escalated_metric_in_message() -> None:
    # cpu уже yellow (молчит), ram green→yellow (алерт) — в сообщении только RAM.
    prev = _state(True, {"cpu": "yellow", "ram": "green", "ssd": "green"})
    state, alerts = evaluate(prev, _online(cpu="yellow", ram="yellow"), name=NAME, ip=IP)
    assert [a.kind for a in alerts] == ["warning"]
    assert "RAM: Нагрузка более 85%" in alerts[0].text
    assert "CPU:" not in alerts[0].text


# ---------------------------------------------- дедуп по зоне и повторный рост (evaluate)
def test_first_elevated_then_same_zone_dedup_silent() -> None:
    # Alert-on-first-elevated: baseline None → yellow алертит; персистнутая yellow-база
    # на следующей итерации (prev == cur) молчит — дедуп по зоне через персист.
    state1, alerts1 = evaluate(None, _online(cpu="yellow"), name=NAME, ip=IP)
    assert [a.kind for a in alerts1] == ["warning"]
    # Следующая итерация: prev = персистнутое state1, зона та же.
    state2, alerts2 = evaluate(state1, _online(cpu="yellow"), name=NAME, ip=IP)
    assert alerts2 == []
    assert state2.zones == {"cpu": "yellow", "ram": "green", "ssd": "green"}


def test_deescalation_to_green_persists_then_regrow_realerts() -> None:
    # Деэскалация red→green молчит, но персистится в green; повторный рост green→red
    # снова алертит (база стала green). Инвариант ADR-014 «повторный рост переалертит».
    prev_red = _state(True, {"cpu": "red", "ram": "green", "ssd": "green"})
    green_state, silent = evaluate(prev_red, _online("green", "green", "green"), name=NAME, ip=IP)
    assert silent == []
    assert green_state.zones == dict(_GREEN)
    # Повторный рост от персистнутой green-базы → снова critical.
    _, realert = evaluate(green_state, _online(cpu="red"), name=NAME, ip=IP)
    assert [a.kind for a in realert] == ["critical"]
