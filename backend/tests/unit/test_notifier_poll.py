"""Unit-тесты цикла опроса NotifierService.poll_once / run (modules/notifier, ADR-014).

Источник состояния — БД (`notifier_server_state`), а не in-memory dict: `prev` читается
через `NotifierServerStateRepository.load_states`, `new_state` пишется `upsert`'ом.
MonitoringService, TelegramClient, репозитории и сессия БД — стабы (без сети/БД); стаб
репозитория состояния хранит строки в словаре, эмулируя персист между итерациями.

Покрывают: PrometheusUnavailable → пропуск без UPSERT; instance отсутствует в ответе →
строка не трогается (нет UPSERT); пустой реестр online → нет записи; alert-on-first-elevated
при отсутствии строки; сопоставление InstanceMetrics по instance; дедуп по зоне через персист
(две poll_once в одной зоне → один алерт); устойчивость run к ошибке итерации и отмене.
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
from app.services.notifier_service import NotifierService

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

    async def commit(self) -> None:
        return None


class _FakeServerRepo:
    servers: ClassVar[list[_FakeServer]] = []

    def __init__(self, session: object) -> None:
        self._session = session

    async def list_online(self) -> list[_FakeServer]:
        return list(_FakeServerRepo.servers)


class _FakeRow:
    """Зеркало строки notifier_server_state для _row_to_state (online + zone_*)."""

    def __init__(
        self,
        online: bool,
        zone_cpu: str | None,
        zone_ram: str | None,
        zone_ssd: str | None,
    ) -> None:
        self.online = online
        self.zone_cpu = zone_cpu
        self.zone_ram = zone_ram
        self.zone_ssd = zone_ssd


class _FakeStateRepo:
    """Стаб репозитория состояния: словарь эмулирует персист notifier_server_state."""

    store: ClassVar[dict[uuid.UUID, _FakeRow]] = {}
    upsert_calls: ClassVar[list[uuid.UUID]] = []

    def __init__(self, session: object) -> None:
        self._session = session

    async def load_states(self, server_ids: list[uuid.UUID]) -> dict[uuid.UUID, _FakeRow]:
        return {sid: _FakeStateRepo.store[sid] for sid in server_ids if sid in _FakeStateRepo.store}

    async def upsert(
        self,
        server_id: uuid.UUID,
        *,
        online: bool,
        zone_cpu: str | None,
        zone_ram: str | None,
        zone_ssd: str | None,
    ) -> None:
        _FakeStateRepo.upsert_calls.append(server_id)
        _FakeStateRepo.store[server_id] = _FakeRow(online, zone_cpu, zone_ram, zone_ssd)


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
    monkeypatch.setattr(ns, "ServerRepository", _FakeServerRepo)
    monkeypatch.setattr(ns, "NotifierServerStateRepository", _FakeStateRepo)
    _FakeServerRepo.servers = []
    _FakeStateRepo.store = {}
    _FakeStateRepo.upsert_calls = []


def _make_service(monitoring: _FakeMonitoring, telegram: _FakeTelegram) -> NotifierService:
    return NotifierService(telegram=telegram, monitoring=monitoring, poll_interval_sec=1)  # type: ignore[arg-type]


def _seed_state(sid: uuid.UUID, *, online: bool, cpu: str, ram: str, ssd: str) -> None:
    _FakeStateRepo.store[sid] = _FakeRow(online, cpu, ram, ssd)


async def test_prometheus_unavailable_skips_iteration_no_upsert(patched_db: None) -> None:
    sid = uuid.uuid4()
    _FakeServerRepo.servers = [_FakeServer(sid, "s1", "10.0.0.1", "10.0.0.1:9100")]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.raise_unavailable = True
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert tg.sent == []
    assert _FakeStateRepo.upsert_calls == []  # состояние в БД НЕ тронуто (нет UPSERT)


async def test_empty_online_registry_no_upsert_rows_preserved(patched_db: None) -> None:
    gone = uuid.uuid4()
    _FakeServerRepo.servers = []  # реестр online пуст
    _seed_state(gone, online=True, cpu="red", ram="green", ssd="green")
    mon = _FakeMonitoring()
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert _FakeStateRepo.upsert_calls == []  # ничего не пишем
    assert gone in _FakeStateRepo.store  # строка сохранена (очистки в цикле нет)
    assert tg.sent == []
    assert mon.calls == []  # Prometheus не опрашивался (нет online-серверов)


async def test_instance_absent_in_response_row_untouched(patched_db: None) -> None:
    sid = uuid.uuid4()
    inst = "10.0.0.8:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "srv", "10.0.0.8", inst)]
    _seed_state(sid, online=True, cpu="yellow", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {}  # instance отсутствует в ответе Prometheus
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert _FakeStateRepo.upsert_calls == []  # строка не трогается (нет UPSERT)
    assert _FakeStateRepo.store[sid].zone_cpu == "yellow"  # прежнее состояние цело
    assert tg.sent == []


async def test_instance_matching_and_alert_on_escalation_upserts(patched_db: None) -> None:
    sid = uuid.uuid4()
    inst = "10.0.0.7:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "web", "10.0.0.7", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert mon.calls == [[inst]]
    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
    assert 'Сервер "web"' in tg.sent[0]
    # Новое состояние UPSERT'нуто в notifier_server_state.
    assert _FakeStateRepo.upsert_calls == [sid]
    assert _FakeStateRepo.store[sid].zone_cpu == "red"


async def test_first_encounter_under_load_alerts_once_and_persists(patched_db: None) -> None:
    # ADR-014 alert-on-first-elevated: строки нет (prev is None) → baseline green →
    # впервые увиденный уже в red даёт ровно один catch-up-алерт, затем персист.
    sid = uuid.uuid4()
    inst = "10.0.0.9:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "hot", "10.0.0.9", inst)]
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red", ram="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)
    # store пуст → prev is None.

    await svc.poll_once()

    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
    assert _FakeStateRepo.store[sid].zone_cpu == "red"
    assert _FakeStateRepo.store[sid].zone_ram == "red"


async def test_dedup_via_persist_two_polls_same_zone_single_alert(patched_db: None) -> None:
    # Две последовательные poll_once в одной зоне → ровно один алерт: первая (prev None,
    # baseline green) алертит и персистит red; вторая видит base == cur → молчит.
    sid = uuid.uuid4()
    inst = "10.0.0.5:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "db", "10.0.0.5", inst)]
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()
    await svc.poll_once()

    assert len(tg.sent) == 1  # дедуп по зоне через персист
    assert _FakeStateRepo.upsert_calls == [sid, sid]  # UPSERT каждую итерацию
    assert _FakeStateRepo.store[sid].zone_cpu == "red"


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
