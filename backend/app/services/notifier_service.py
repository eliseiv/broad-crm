"""Telegram-нотификатор: фоновый опрос online-серверов и алерты при эскалации.

Фоновая asyncio-задача внутри backend-процесса (ADR-009). State персистится в БД
(`notifier_server_state`, ADR-014) — читается из БД каждую итерацию (источник истины,
переживает рестарт/деплой, TD-019 закрыт). Алерт только при ПОВЫШЕНИИ зоны нагрузки
или online→offline; деэскалация/восстановление — молча, но персистятся. Отсутствие
строки (`prev is None`) трактуется как здоровый baseline (online + green×3) →
alert-on-first-elevated. Полная семантика переходов — modules/notifier (State-машина).
Пороги зон переиспользуются из `app.domain.thresholds` (usage_to_zone) через
MonitoringService — не дублируются.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal, cast

from app.db import get_sessionmaker
from app.domain.notifications import (
    METRIC_LABELS,
    MetricItem,
    build_critical_load,
    build_offline,
    build_warning,
)
from app.domain.thresholds import Zone
from app.infra.telegram import TelegramClient
from app.logging import get_logger
from app.models.notifier_server_state import NotifierServerState
from app.repositories.notifier_server_state_repository import (
    NotifierServerStateRepository,
)
from app.repositories.server_repository import ServerRepository
from app.services.monitoring_service import (
    InstanceMetrics,
    MonitoringService,
    PrometheusUnavailable,
)

logger = get_logger(__name__)

# Метрики в стабильном порядке (CPU, RAM, SSD).
_METRIC_KEYS: tuple[str, ...] = ("cpu", "ram", "ssd")
# Ранг зоны для сравнения эскалации: green=0 < yellow=1 < red=2.
_ZONE_RANK: dict[Zone, int] = {"green": 0, "yellow": 1, "red": 2}

AlertKind = Literal["warning", "critical", "offline"]


@dataclass(frozen=True)
class ServerAlertState:
    """In-memory состояние сервера: доступность и зоны метрик (None при offline/без метрик)."""

    online: bool
    zones: dict[str, Zone] | None


@dataclass(frozen=True)
class Alert:
    """Готовое сообщение алерта: тип + plain-текст для Telegram."""

    kind: AlertKind
    text: str


# Здоровый baseline при отсутствии персистнутой строки (prev is None): online +
# все зоны green (ADR-014, alert-on-first-elevated). Разделяемый read-only объект —
# ServerAlertState заморожен и его zones не мутируется.
_GREEN_BASELINE_ZONES: dict[str, Zone] = {"cpu": "green", "ram": "green", "ssd": "green"}
_HEALTHY_BASELINE = ServerAlertState(online=True, zones=_GREEN_BASELINE_ZONES)


def _rank(zone: Zone | None) -> int:
    """Ранг зоны; базовая зона None ≡ green (rank 0) при сравнении эскалации."""
    return _ZONE_RANK[zone] if zone is not None else 0


def evaluate(
    prev: ServerAlertState | None,
    im: InstanceMetrics,
    *,
    name: str,
    ip: str,
) -> tuple[ServerAlertState, list[Alert]]:
    """Чистая функция перехода state-машины (modules/notifier).

    Возвращает новое состояние и список алертов (тип + текст) для отправки.
    Отсутствие базы (`prev is None`) трактуется как здоровый baseline
    (online + green×3, ADR-014): сервер, впервые увиденный уже в yellow/red/offline,
    получает ровно один catch-up-алерт (alert-on-first-elevated). Алерт только при
    `rank(cur) > rank(base)` (эскалация зоны) и при online→offline; деэскалация и
    восстановление offline→online — молча. Тестируется qa без сети/БД.
    """
    base_state = prev if prev is not None else _HEALTHY_BASELINE

    # --- offline (up == 0): метрики не оцениваются, zones сбрасываются ---
    if not im.online:
        alerts: list[Alert] = []
        if base_state.online:
            # online → offline (в т.ч. baseline online → offline): срочное сообщение.
            alerts.append(Alert("offline", build_offline(name, ip)))
        return ServerAlertState(online=False, zones=None), alerts

    # --- online, но метрики недоступны: зоны не оцениваются, zone_* пишутся NULL ---
    if im.metrics is None:
        return ServerAlertState(online=True, zones=None), []

    # --- online + есть метрики ---
    metrics_map = {"cpu": im.metrics.cpu, "ram": im.metrics.ram, "ssd": im.metrics.ssd}
    cur_zones: dict[str, Zone] = {}
    for key in _METRIC_KEYS:
        zone = metrics_map[key].zone
        cur_zones[key] = zone if zone is not None else "green"
    new_state = ServerAlertState(online=True, zones=cur_zones)

    # База сравнения: возврат offline→online ⇒ green по всем (rank 0) ⇒ переалерт;
    # online→online ⇒ зоны базы (None-зона трактуется как green в _rank).
    base: dict[str, Zone] = {} if not base_state.online else (base_state.zones or {})

    warning_items: list[MetricItem] = []
    critical_items: list[MetricItem] = []
    for key in _METRIC_KEYS:
        cur = cur_zones[key]
        if _rank(cur) <= _rank(base.get(key)):
            continue  # не повышение — деэскалация/без изменений, молча
        percent = metrics_map[key].usage_percent
        if percent is None:
            continue
        label = METRIC_LABELS[key]
        if cur == "yellow":
            warning_items.append((label, percent))
        elif cur == "red":
            critical_items.append((label, percent))

    alerts = []
    if warning_items:
        alerts.append(Alert("warning", build_warning(name, ip, warning_items)))
    if critical_items:
        alerts.append(Alert("critical", build_critical_load(name, ip, critical_items)))
    return new_state, alerts


def _row_to_state(row: NotifierServerState) -> ServerAlertState:
    """Строка `notifier_server_state` → логическая форма ServerAlertState.

    Offline ⇒ zones=None. Online ⇒ зоны из non-NULL колонок; любая NULL-зона
    опускается (≡ отсутствует, трактуется как green при сравнении). Пустой набор
    зон (online без метрик) ⇒ zones=None. Значения из БД гарантированы CHECK'ом.
    """
    if not row.online:
        return ServerAlertState(online=False, zones=None)
    zones: dict[str, Zone] = {}
    raw = {"cpu": row.zone_cpu, "ram": row.zone_ram, "ssd": row.zone_ssd}
    for key, value in raw.items():
        if value is not None:
            zones[key] = cast(Zone, value)
    return ServerAlertState(online=True, zones=zones or None)


def _state_to_columns(state: ServerAlertState) -> tuple[Zone | None, Zone | None, Zone | None]:
    """ServerAlertState → (zone_cpu, zone_ram, zone_ssd) для UPSERT.

    zones=None (offline / online без метрик) ⇒ все NULL.
    """
    if state.zones is None:
        return None, None, None
    return (state.zones.get("cpu"), state.zones.get("ram"), state.zones.get("ssd"))


class NotifierService:
    """Читает состояние из БД, выполняет цикл опроса (опрос → sleep) и персистит."""

    def __init__(
        self,
        *,
        telegram: TelegramClient,
        monitoring: MonitoringService,
        poll_interval_sec: int,
        metric_window_sec: int,
    ) -> None:
        self._telegram = telegram
        self._monitoring = monitoring
        self._poll_interval_sec = poll_interval_sec
        # Окно max-over-window для оценки зоны CPU/RAM/SSD (ADR-016). Уже
        # скламплено к poll_interval в config (notifier_metric_window_effective_sec).
        self._metric_window_sec = metric_window_sec

    async def poll_once(self) -> None:
        """Одна итерация опроса (modules/notifier «Итерация опроса»).

        Короткая сессия читает `list_online()` + персистнутое состояние из БД
        (источник истины, ADR-014) и закрывается до запроса к Prometheus.
        `PrometheusUnavailable` → итерация пропускается, состояние в БД НЕ трогается.
        После отправки алертов новое состояние UPSERT'ится отдельной короткой сессией.
        """
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            server_repo = ServerRepository(session)
            servers = await server_repo.list_online()
            snapshot = {
                server.id: (server.name, str(server.ip), server.instance) for server in servers
            }
            state_repo = NotifierServerStateRepository(session)
            rows = await state_repo.load_states(list(snapshot.keys()))
            prev_states = {sid: _row_to_state(row) for sid, row in rows.items()}
        # Сессия БД закрыта — далее только Prometheus/Telegram.

        if not snapshot:
            # Реестр online пуст — опрашивать нечего; строки состояния сохраняются.
            return

        instances = [instance for (_, _, instance) in snapshot.values()]
        try:
            # Windowed-режим (ADR-016): зона CPU/RAM/SSD по максимуму за окно
            # опроса; online/uptime/detail остаются мгновенными.
            metrics_by_instance = await self._monitoring.fetch_for_instances(
                instances, window_sec=self._metric_window_sec
            )
        except PrometheusUnavailable:
            logger.warning("notifier_prometheus_unavailable")
            return  # состояние в БД НЕ трогаем

        to_persist: list[tuple[uuid.UUID, ServerAlertState]] = []
        for server_id, (name, ip, instance) in snapshot.items():
            prev = prev_states.get(server_id)
            im = metrics_by_instance.get(instance)
            if im is None:
                # Instance отсутствует в ответе — не оцениваем, строку не трогаем.
                continue
            state, alerts = evaluate(prev, im, name=name, ip=ip)
            to_persist.append((server_id, state))
            for alert in alerts:
                await self._telegram.send_message(alert.text)

        if not to_persist:
            return
        # UPSERT состояния — отдельной короткой сессией, независимо от результата
        # доставки в Telegram (best-effort доставка не влияет на состояние).
        async with sessionmaker() as session:
            state_repo = NotifierServerStateRepository(session)
            for server_id, state in to_persist:
                zone_cpu, zone_ram, zone_ssd = _state_to_columns(state)
                await state_repo.upsert(
                    server_id,
                    online=state.online,
                    zone_cpu=zone_cpu,
                    zone_ram=zone_ram,
                    zone_ssd=zone_ssd,
                )
            await session.commit()

    async def run(self) -> None:
        """Бесконечный цикл: опрос → sleep. Ошибка итерации логируется, цикл живёт."""
        logger.info("notifier_started", interval=self._poll_interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error("notifier_poll_failed", error_type=type(exc).__name__)
                await asyncio.sleep(self._poll_interval_sec)
        except asyncio.CancelledError:
            logger.info("notifier_stopped")
            raise


__all__ = [
    "Alert",
    "AlertKind",
    "NotifierService",
    "ServerAlertState",
    "evaluate",
]
