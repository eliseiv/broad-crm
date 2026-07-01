"""Unit-тесты цикла опроса NotifierService.poll_once / run (modules/notifier).

MonitoringService, TelegramClient, репозиторий и сессия БД — стабы (без сети/БД).
Покрывают: PrometheusUnavailable → пропуск без изменений state; очистка
исчезнувших серверов; сопоставление InstanceMetrics по instance; отправка алертов
при эскалации; устойчивость run к ошибке итерации и отмене.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import ClassVar

import pytest
from app.domain.thresholds import Zone
from app.schemas.metrics import Metric, MetricDetail, ServerMetrics
from app.services import notifier_service as ns
from app.services.monitoring_service import InstanceMetrics, PrometheusUnavailable
from app.services.notifier_service import NotifierService, ServerAlertState

_PCT: dict[Zone, float] = {"green": 10.0, "yellow": 85.0, "red": 95.0}


def _online(cpu: Zone = "green", ram: Zone = "green", ssd: Zone = "green") -> InstanceMetrics:
    def m(z: Zone) -> Metric:
        return Metric(
            usage_percent=_PCT[z], zone=z, detail=MetricDetail(value=None, total=8, unit="cores")
        )

    return InstanceMetrics(
        online=True,
        uptime_seconds=1,
        last_updated=None,
        metrics=ServerMetrics(cpu=m(cpu), ram=m(ram), ssd=m(ssd)),
    )


class _FakeServer:
    def __init__(self, sid: uuid.UUID, name: str, ip: str, instance: str) -> None:
        self.id = sid
        self.name = name
        self.ip = ip
        self.instance = instance


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeRepo:
    servers: ClassVar[list[_FakeServer]] = []

    def __init__(self, session: object) -> None:
        self._session = session

    async def list_online(self) -> list[_FakeServer]:
        return list(_FakeRepo.servers)


class _FakeMonitoring:
    def __init__(self) -> None:
        self.result: dict[str, InstanceMetrics] = {}
        self.raise_unavailable = False
        self.calls: list[list[str]] = []

    async def fetch_for_instances(self, instances: list[str]) -> dict[str, InstanceMetrics]:
        self.calls.append(list(instances))
        if self.raise_unavailable:
            raise PrometheusUnavailable("prom down")
        return self.result


class _FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> bool:
        self.sent.append(text)
        return True


@pytest.fixture
def patched_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ns, "get_sessionmaker", lambda: (lambda: _FakeSession()))
    monkeypatch.setattr(ns, "ServerRepository", _FakeRepo)
    _FakeRepo.servers = []


def _make_service(monitoring: _FakeMonitoring, telegram: _FakeTelegram) -> NotifierService:
    return NotifierService(telegram=telegram, monitoring=monitoring, poll_interval_sec=1)  # type: ignore[arg-type]


async def test_prometheus_unavailable_skips_iteration_state_untouched(
    patched_db: None,
) -> None:
    sid = uuid.uuid4()
    _FakeRepo.servers = [_FakeServer(sid, "s1", "10.0.0.1", "10.0.0.1:9100")]
    mon = _FakeMonitoring()
    mon.raise_unavailable = True
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    preset = {
        sid: ServerAlertState(online=True, zones={"cpu": "green", "ram": "green", "ssd": "green"})
    }
    svc._state = dict(preset)

    await svc.poll_once()

    assert tg.sent == []
    assert svc._state == preset  # state НЕ тронут


async def test_server_disappeared_removed_from_state_silently(patched_db: None) -> None:
    gone = uuid.uuid4()
    _FakeRepo.servers = []  # реестр online пуст
    mon = _FakeMonitoring()
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    svc._state = {
        gone: ServerAlertState(online=True, zones={"cpu": "red", "ram": "green", "ssd": "green"})
    }

    await svc.poll_once()

    assert svc._state == {}
    assert tg.sent == []
    assert mon.calls == []  # Prometheus не опрашивался (нет online-серверов)


async def test_subset_disappeared_removed_others_kept(patched_db: None) -> None:
    keep = uuid.uuid4()
    gone = uuid.uuid4()
    inst = "10.0.0.1:9100"
    _FakeRepo.servers = [_FakeServer(keep, "keep", "10.0.0.1", inst)]
    mon = _FakeMonitoring()
    mon.result = {inst: _online("green")}  # без эскалации
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    svc._state = {
        keep: ServerAlertState(online=True, zones={"cpu": "green", "ram": "green", "ssd": "green"}),
        gone: ServerAlertState(online=True, zones={"cpu": "green", "ram": "green", "ssd": "green"}),
    }

    await svc.poll_once()

    assert keep in svc._state
    assert gone not in svc._state
    assert tg.sent == []


async def test_instance_matching_and_alert_on_escalation(patched_db: None) -> None:
    sid = uuid.uuid4()
    inst = "10.0.0.7:9100"
    _FakeRepo.servers = [_FakeServer(sid, "web", "10.0.0.7", inst)]
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    svc._state = {
        sid: ServerAlertState(online=True, zones={"cpu": "green", "ram": "green", "ssd": "green"})
    }

    await svc.poll_once()

    assert mon.calls == [[inst]]
    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
    assert 'Сервер "web"' in tg.sent[0]
    assert svc._state[sid].zones == {"cpu": "red", "ram": "green", "ssd": "green"}


async def test_instance_absent_in_response_keeps_prev_state(patched_db: None) -> None:
    sid = uuid.uuid4()
    inst = "10.0.0.8:9100"
    _FakeRepo.servers = [_FakeServer(sid, "srv", "10.0.0.8", inst)]
    mon = _FakeMonitoring()
    mon.result = {}  # instance отсутствует в ответе Prometheus
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    prev = ServerAlertState(online=True, zones={"cpu": "yellow", "ram": "green", "ssd": "green"})
    svc._state = {sid: prev}

    await svc.poll_once()

    assert svc._state[sid] == prev  # сохранён прежний state
    assert tg.sent == []


async def test_first_encounter_under_load_no_alert(patched_db: None) -> None:
    sid = uuid.uuid4()
    inst = "10.0.0.9:9100"
    _FakeRepo.servers = [_FakeServer(sid, "hot", "10.0.0.9", inst)]
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red", ram="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    # state пуст → первая встреча, база фиксируется без алерта.

    await svc.poll_once()

    assert tg.sent == []
    assert svc._state[sid].zones == {"cpu": "red", "ram": "red", "ssd": "green"}


# ---------------------------------------------------------------- run()
async def test_run_handles_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    mon = _FakeMonitoring()
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    calls: list[int] = []

    async def fake_poll() -> None:
        calls.append(1)

    async def fake_sleep(_d: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(svc, "poll_once", fake_poll)
    monkeypatch.setattr(ns.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await svc.run()

    assert calls == [1]


async def test_run_survives_iteration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    mon = _FakeMonitoring()
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    state = {"n": 0}

    async def fake_poll() -> None:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")  # первая итерация падает

    async def fake_sleep(_d: float) -> None:
        if state["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(svc, "poll_once", fake_poll)
    monkeypatch.setattr(ns.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await svc.run()

    assert state["n"] == 2  # пережил ошибку первой итерации, выполнил вторую
