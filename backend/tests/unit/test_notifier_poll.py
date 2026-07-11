"""Unit-тесты цикла опроса NotifierService.poll_once / run (modules/notifier, ADR-014).

Источник состояния — БД (`notifier_server_state`), а не in-memory dict: `prev` читается
через `NotifierServerStateRepository.load_states`, `new_state` пишется `upsert`'ом.
MonitoringService, TelegramClient, репозитории и сессия БД — стабы (без сети/БД); стаб
репозитория состояния хранит строки в словаре, эмулируя персист между итерациями.

Покрывают: PrometheusUnavailable → пропуск без UPSERT и без записи лога; instance
отсутствует в ответе → строка не трогается (нет UPSERT); пустой реестр online → нет записи;
alert-on-first-elevated при отсутствии строки; сопоставление InstanceMetrics по instance;
дедуп по зоне через персист (две poll_once в одной зоне → один алерт); durable-лог
notifier_alert_log (ADR-018): одна строка на каждый отправленный алерт с delivered =
результат send_message, message без секретов, recovery-алерт логируется; устойчивость run
к ошибке итерации и отмене.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import ClassVar

import pytest
from app.domain.thresholds import Zone
from app.models.notifier_alert_log import NotifierAlertLog
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
    """Стаб сессии: собирает добавленные ORM-объекты (строки notifier_alert_log).

    `add` вызывает реальный NotifierAlertLogRepository (не замокан) — так проверяем,
    что durable-лог пишется в финальной сессии с корректными полями (ADR-018).
    Добавленные объекты складываются в ClassVar (сброс — в фикстуре patched_db).
    """

    added: ClassVar[list[object]] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def add(self, obj: object) -> None:
        _FakeSession.added.append(obj)

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


class _FakeBackend:
    """Строка `backends` для перечня бэков в алерте (ADR-046 §1): position/code/name/domain."""

    def __init__(self, *, position: int, code: str, name: str, domain: str) -> None:
        self.position = position
        self.code = code
        self.name = name
        self.domain = domain


class _FakeBackendRepo:
    """Стаб BackendRepository: `_attach_backends` открывает ДОП. короткую сессию (ADR-046 §1).

    Перечень резолвится ТОЛЬКО для алертящих серверов — стаб копит запрошенные server_id,
    чтобы это можно было проверить. По умолчанию бэков нет → блок «Бэки:» не добавляется и
    текст сообщения побайтово равен прежнему.
    """

    by_server: ClassVar[dict[uuid.UUID, list[_FakeBackend]]] = {}
    calls: ClassVar[list[uuid.UUID]] = []

    def __init__(self, session: object) -> None:
        self._session = session

    async def list_by_server(self, server_id: uuid.UUID) -> list[_FakeBackend]:
        _FakeBackendRepo.calls.append(server_id)
        return list(_FakeBackendRepo.by_server.get(server_id, []))


class _FakeMonitoring:
    def __init__(self) -> None:
        self.result: dict[str, InstanceMetrics] = {}
        self.raise_unavailable = False
        self.calls: list[list[str]] = []
        # Окна, с которыми нотификатор вызвал fetch_for_instances (ADR-016):
        # ожидается effective metric_window_sec, а не None (windowed-режим).
        self.window_calls: list[int | None] = []

    async def fetch_for_instances(
        self, instances: list[str], window_sec: int | None = None
    ) -> dict[str, InstanceMetrics]:
        self.calls.append(list(instances))
        self.window_calls.append(window_sec)
        if self.raise_unavailable:
            raise PrometheusUnavailable("prom down")
        return self.result


class _FakeTelegram:
    def __init__(self, *, deliver: bool = True) -> None:
        self.sent: list[str] = []
        self._deliver = deliver  # результат send_message → delivered в логе

    async def send_message(self, text: str) -> bool:
        self.sent.append(text)
        return self._deliver


@pytest.fixture
def patched_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ns, "get_sessionmaker", lambda: (lambda: _FakeSession()))
    monkeypatch.setattr(ns, "ServerRepository", _FakeServerRepo)
    monkeypatch.setattr(ns, "NotifierServerStateRepository", _FakeStateRepo)
    # `_attach_backends` (ADR-046 §1) открывает доп. короткую сессию и читает бэки
    # алертящих серверов через BackendRepository — стабим и его (сети/БД в unit нет).
    monkeypatch.setattr(ns, "BackendRepository", _FakeBackendRepo)
    _FakeServerRepo.servers = []
    _FakeStateRepo.store = {}
    _FakeStateRepo.upsert_calls = []
    _FakeSession.added = []
    _FakeBackendRepo.by_server = {}
    _FakeBackendRepo.calls = []


def _alert_logs() -> list[NotifierAlertLog]:
    """Строки notifier_alert_log, добавленные в финальной сессии итерации."""
    return [obj for obj in _FakeSession.added if isinstance(obj, NotifierAlertLog)]


_METRIC_WINDOW_SEC = 90


def _make_service(
    monitoring: _FakeMonitoring,
    telegram: _FakeTelegram,
    *,
    metric_window_sec: int = _METRIC_WINDOW_SEC,
) -> NotifierService:
    return NotifierService(
        telegram=telegram,  # type: ignore[arg-type]
        monitoring=monitoring,  # type: ignore[arg-type]
        poll_interval_sec=1,
        metric_window_sec=metric_window_sec,
    )


def _seed_state(
    sid: uuid.UUID,
    *,
    online: bool,
    cpu: str | None,
    ram: str | None,
    ssd: str | None,
) -> None:
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


async def test_notifier_fetches_with_effective_window_max_over_window_zone(
    patched_db: None,
) -> None:
    # ADR-016: нотификатор оценивает зону по max-за-окно — вызывает
    # fetch_for_instances с window_sec=metric_window_sec (не None/instant).
    # Зона red из max-за-окно результата продолжает эскалировать/персистить (ADR-014).
    sid = uuid.uuid4()
    inst = "10.0.0.4:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "spike", "10.0.0.4", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}  # max-за-окно CPU в red-зоне
    tg = _FakeTelegram()
    svc = _make_service(mon, tg, metric_window_sec=120)

    await svc.poll_once()

    # Windowed-режим: окно передано, а не мгновенный запрос (None).
    assert mon.window_calls == [120]
    assert mon.calls == [[inst]]
    # Зона max-за-окно продолжает работать сквозь evaluate/персист (ADR-014).
    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
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


# -------------------------------------------------- durable-лог алертов (ADR-018)
async def test_alert_log_one_row_per_sent_alert_delivered_true(patched_db: None) -> None:
    # ADR-018: одна строка notifier_alert_log на КАЖДЫЙ отправленный алерт;
    # delivered = результат send_message (True); message = plain-текст без секретов.
    sid = uuid.uuid4()
    inst = "10.0.0.11:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "web", "10.0.0.11", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}
    tg = _FakeTelegram()  # send_message → True
    svc = _make_service(mon, tg)

    await svc.poll_once()

    logs = _alert_logs()
    assert len(logs) == 1  # ровно одна строка на один отправленный алерт
    row = logs[0]
    assert row.server_id == sid
    assert row.kind == "critical"
    assert row.delivered is True
    assert row.message == tg.sent[0]  # тот же отправленный текст
    # Секретов в message нет: только тело алерта (имя/IP), без токена/chat_id/URL.
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in row.message
    assert "http" not in row.message
    assert "bot" not in row.message.lower()


async def test_alert_log_delivered_false_when_send_fails(patched_db: None) -> None:
    # send_message вернул False (исчерпаны ретраи) → строка всё равно пишется с
    # delivered=False (в этом смысл лога — доказать попытку, ADR-018).
    sid = uuid.uuid4()
    inst = "10.0.0.13:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "db", "10.0.0.13", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}
    tg = _FakeTelegram(deliver=False)
    svc = _make_service(mon, tg)

    await svc.poll_once()

    logs = _alert_logs()
    assert len(logs) == 1
    assert logs[0].delivered is False


async def test_alert_log_multiple_rows_per_iteration_mixed_kinds(patched_db: None) -> None:
    # Смешанная эскалация за опрос: warning + critical → две отправки → две строки лога.
    sid = uuid.uuid4()
    inst = "10.0.0.14:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "mix", "10.0.0.14", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="yellow", ram="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    logs = _alert_logs()
    assert sorted(row.kind for row in logs) == ["critical", "warning"]
    assert all(row.server_id == sid and row.delivered is True for row in logs)


async def test_recovery_offline_to_online_sends_and_logs_recovered(patched_db: None) -> None:
    # prev.online=False → online (здоров): 🟢 ВОССТАНОВЛЕНО отправлено и залогировано
    # (kind='recovered'), состояние online=True персистится (ADR-018).
    sid = uuid.uuid4()
    inst = "10.0.0.12:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "cam", "10.0.0.12", inst)]
    _seed_state(sid, online=False, cpu=None, ram=None, ssd=None)  # был offline
    mon = _FakeMonitoring()
    mon.result = {inst: _online("green", "green", "green")}  # вернулся здоровым
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert len(tg.sent) == 1
    assert "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢" in tg.sent[0]
    logs = _alert_logs()
    assert [row.kind for row in logs] == ["recovered"]
    assert logs[0].delivered is True
    assert _FakeStateRepo.store[sid].online is True


async def test_prometheus_unavailable_no_alert_log_written(patched_db: None) -> None:
    # PrometheusUnavailable → итерация пропущена целиком: ни UPSERT, ни строки лога.
    sid = uuid.uuid4()
    _FakeServerRepo.servers = [_FakeServer(sid, "s1", "10.0.0.1", "10.0.0.1:9100")]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.raise_unavailable = True
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert _alert_logs() == []  # лог алертов не пишется
    assert _FakeStateRepo.upsert_calls == []


async def test_no_alert_no_log_row_on_silent_iteration(patched_db: None) -> None:
    # Молчаливая итерация (зона без изменений): UPSERT есть, но строк лога нет.
    sid = uuid.uuid4()
    inst = "10.0.0.15:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "quiet", "10.0.0.15", inst)]
    _seed_state(sid, online=True, cpu="yellow", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="yellow")}  # та же зона → молча
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert tg.sent == []
    assert _alert_logs() == []  # нет отправок → нет строк лога
    assert _FakeStateRepo.upsert_calls == [sid]  # состояние всё равно персистится


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


# --------------------------------------------------------------------------------------
# Wiring блока «Бэки:» в РЕАЛЬНОМ пути доставки (ADR-046 §1): `_attach_backends` открывает
# доп. короткую сессию, читает бэки алертящего сервера и ПОВТОРНО зовёт `evaluate` с ними.
# Билдеры покрыты побайтово отдельно (test_notifications_backends_block.py) — здесь
# проверяется, что перечень реально доезжает до ОТПРАВЛЕННОГО сообщения, что резолв идёт
# ТОЛЬКО для алертящих серверов (перф-инвариант §1) и что второй `evaluate` не портит
# state-машину.
# --------------------------------------------------------------------------------------
def _seed_backends(sid: uuid.UUID) -> None:
    """Два бэка сервера, поданных в ОБРАТНОМ требуемом порядке (сортировка — на нашей стороне)."""
    _FakeBackendRepo.by_server[sid] = [
        _FakeBackend(position=1, code="web", name="Web", domain="https://web.example.com"),
        _FakeBackend(position=0, code="api-eu", name="API EU", domain="https://eu.example.com"),
    ]


async def test_critical_alert_message_carries_backends_block_byte_exact(
    patched_db: None,
) -> None:
    sid = uuid.uuid4()
    inst = "10.0.0.7:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "web", "10.0.0.7", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    _seed_backends(sid)
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert len(tg.sent) == 1
    # Побайтово — по modules/notifier «Блок "Бэки:" в алертах об ОШИБКАХ»; порядок перечня
    # `position ASC, code ASC` (подан обратный → сортировка сделана in-memory).
    assert tg.sent[0] == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Сервер "web"\n'
        "IP 10.0.0.7\n"
        "\n"
        "CPU: Нагрузка более 95%\n"
        "\n"
        "Бэки:\n"
        'Бэк "API EU" [api-eu] https://eu.example.com\n'
        'Бэк "Web" [web] https://web.example.com'
    )
    # В durable-лог (ADR-018) уходит ОТПРАВЛЕННАЯ строка целиком, включая блок «Бэки:».
    logs = _alert_logs()
    assert [row.kind for row in logs] == ["critical"]
    assert logs[0].message == tg.sent[0]


async def test_offline_alert_message_carries_backends_block(patched_db: None) -> None:
    sid = uuid.uuid4()
    inst = "10.0.0.8:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "db", "10.0.0.8", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    _FakeBackendRepo.by_server[sid] = [
        _FakeBackend(position=0, code="api-eu", name="API EU", domain="https://eu.example.com")
    ]
    mon = _FakeMonitoring()
    mon.result = {
        inst: InstanceMetrics(online=False, uptime_seconds=None, last_updated=None, metrics=None)
    }
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert tg.sent[0] == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Сервер "db"\n'
        "IP 10.0.0.8\n"
        "\n"
        "Сервер не доступен\n"
        "\n"
        "Бэки:\n"
        'Бэк "API EU" [api-eu] https://eu.example.com'
    )


async def test_backends_resolved_only_for_alerting_servers(patched_db: None) -> None:
    """Перф-инвариант ADR-046 §1: SELECT бэков делается ТОЛЬКО для алертящих серверов."""
    alerting = uuid.uuid4()
    healthy = uuid.uuid4()
    inst_a, inst_h = "10.0.0.7:9100", "10.0.0.8:9100"
    _FakeServerRepo.servers = [
        _FakeServer(alerting, "web", "10.0.0.7", inst_a),
        _FakeServer(healthy, "idle", "10.0.0.8", inst_h),
    ]
    _seed_state(alerting, online=True, cpu="green", ram="green", ssd="green")
    _seed_state(healthy, online=True, cpu="green", ram="green", ssd="green")
    _seed_backends(alerting)
    _seed_backends(healthy)  # у здорового бэки ЕСТЬ, но их не должны запрашивать
    mon = _FakeMonitoring()
    mon.result = {inst_a: _online(cpu="red"), inst_h: _online()}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert _FakeBackendRepo.calls == [alerting]
    assert len(tg.sent) == 1
    assert "Бэки:" in tg.sent[0]


async def test_recovery_alert_does_not_resolve_backends(patched_db: None) -> None:
    """Recovery перечнем НЕ расширяется (решение владельца) → и SELECT'а бэков не делает."""
    sid = uuid.uuid4()
    inst = "10.0.0.12:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "cam", "10.0.0.12", inst)]
    _seed_state(sid, online=False, cpu=None, ram=None, ssd=None)
    _seed_backends(sid)
    mon = _FakeMonitoring()
    mon.result = {inst: _online("green", "green", "green")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert _FakeBackendRepo.calls == []  # резолв не вызывался вовсе
    assert "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢" in tg.sent[0]
    assert "Бэки:" not in tg.sent[0]


async def test_no_backends_for_alerting_server_keeps_message_byte_equal(
    patched_db: None,
) -> None:
    """Бэков у алертящего сервера нет → блок не добавляется (сообщение как прежде)."""
    sid = uuid.uuid4()
    inst = "10.0.0.7:9100"
    _FakeServerRepo.servers = [_FakeServer(sid, "web", "10.0.0.7", inst)]
    _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
    mon = _FakeMonitoring()
    mon.result = {inst: _online(cpu="red")}
    tg = _FakeTelegram()
    svc = _make_service(mon, tg)

    await svc.poll_once()

    assert _FakeBackendRepo.calls == [sid]  # спросили…
    assert "Бэки:" not in tg.sent[0]  # …но перечень пуст → блока нет
    assert tg.sent[0] == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n" 'Сервер "web"\n' "IP 10.0.0.7\n" "\n" "CPU: Нагрузка более 95%"
    )


async def test_persisted_state_identical_with_and_without_backends(patched_db: None) -> None:
    """`_attach_backends` зовёт `evaluate` ВТОРОЙ раз, и персистится результат ВТОРОГО вызова.

    Корректность state-машины теперь молча держится на чистоте `evaluate` (функция без
    сети/БД, состояние от `backends` не зависит — меняется только текст сообщений).
    Фиксируем это: состояние, ушедшее в UPSERT, обязано быть идентично состоянию при
    пустом перечне бэков.
    """
    inst = "10.0.0.7:9100"

    async def _run(with_backends: bool) -> tuple[list[uuid.UUID], tuple[object, ...]]:
        sid = uuid.uuid4()
        _FakeServerRepo.servers = [_FakeServer(sid, "web", "10.0.0.7", inst)]
        _FakeStateRepo.store = {}
        _FakeStateRepo.upsert_calls = []
        _FakeBackendRepo.by_server = {}
        _FakeBackendRepo.calls = []
        _seed_state(sid, online=True, cpu="green", ram="green", ssd="green")
        if with_backends:
            _seed_backends(sid)
        mon = _FakeMonitoring()
        mon.result = {inst: _online(cpu="red", ram="yellow")}
        tg = _FakeTelegram()
        await _make_service(mon, tg).poll_once()
        row = _FakeStateRepo.store[sid]
        return (
            [uuid.UUID(int=0) for _ in _FakeStateRepo.upsert_calls],  # число UPSERT'ов
            (row.online, row.zone_cpu, row.zone_ram, row.zone_ssd),
        )

    calls_without, state_without = await _run(with_backends=False)
    calls_with, state_with = await _run(with_backends=True)

    assert calls_with == calls_without  # столько же UPSERT'ов
    assert state_with == state_without  # и то же самое состояние
    assert state_with == (True, "red", "yellow", "green")
