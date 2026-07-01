"""Telegram-нотификатор: фоновый опрос online-серверов и алерты при эскалации.

Фоновая asyncio-задача внутри backend-процесса (ADR-009). State — in-memory
(рестарт сбрасывает, TD-019). Алерт только при ПОВЫШЕНИИ зоны нагрузки или
online→offline; деэскалация/восстановление — молча. Полная семантика переходов —
modules/notifier (State-машина). Пороги зон переиспользуются из
`app.domain.thresholds` (usage_to_zone) через MonitoringService — не дублируются.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Literal

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
    Алерт только при `rank(cur) > rank(base)` (эскалация зоны) и при online→offline;
    деэскалация и восстановление offline→online — молча. Тестируется qa без сети/БД.
    """
    # --- offline (up == 0): метрики не оцениваются, zones сбрасываются ---
    if not im.online:
        alerts: list[Alert] = []
        if prev is not None and prev.online:
            # online → offline: одно срочное сообщение.
            alerts.append(Alert("offline", build_offline(name, ip)))
        return ServerAlertState(online=False, zones=None), alerts

    # --- online, но метрики недоступны: зоны не оцениваются, state сохраняется ---
    if im.metrics is None:
        zones = prev.zones if prev is not None else None
        return ServerAlertState(online=True, zones=zones), []

    # --- online + есть метрики ---
    metrics_map = {"cpu": im.metrics.cpu, "ram": im.metrics.ram, "ssd": im.metrics.ssd}
    cur_zones: dict[str, Zone] = {}
    for key in _METRIC_KEYS:
        zone = metrics_map[key].zone
        cur_zones[key] = zone if zone is not None else "green"
    new_state = ServerAlertState(online=True, zones=cur_zones)

    # Первая встреча online — фиксируем зоны как базу, алертов нет.
    if prev is None:
        return new_state, []

    # База сравнения: возврат offline→online ⇒ green по всем (rank 0) ⇒ переалерт;
    # online→online ⇒ предыдущие зоны (None-зона трактуется как green в _rank).
    base: dict[str, Zone] = {} if not prev.online else (prev.zones or {})

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


class NotifierService:
    """Держит in-memory state и выполняет цикл опроса (опрос → sleep)."""

    def __init__(
        self,
        *,
        telegram: TelegramClient,
        monitoring: MonitoringService,
        poll_interval_sec: int,
    ) -> None:
        self._telegram = telegram
        self._monitoring = monitoring
        self._poll_interval_sec = poll_interval_sec
        self._state: dict[uuid.UUID, ServerAlertState] = {}

    async def poll_once(self) -> None:
        """Одна итерация опроса (modules/notifier «Итерация опроса»).

        Сессия БД открывается коротко и закрывается до запроса к Prometheus.
        `PrometheusUnavailable` → итерация пропускается, state не изменяется.
        Серверы, исчезнувшие из реестра online, удаляются из state молча.
        """
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            repo = ServerRepository(session)
            servers = await repo.list_online()
            snapshot = {
                server.id: (server.name, str(server.ip), server.instance) for server in servers
            }
        # Сессия БД закрыта — далее только Prometheus/Telegram.

        if not snapshot:
            # Реестр online пуст — все прежние серверы исчезли, state очищается.
            self._state = {}
            return

        instances = [instance for (_, _, instance) in snapshot.values()]
        try:
            metrics_by_instance = await self._monitoring.fetch_for_instances(instances)
        except PrometheusUnavailable:
            logger.warning("notifier_prometheus_unavailable")
            return  # state НЕ трогаем

        new_state: dict[uuid.UUID, ServerAlertState] = {}
        for server_id, (name, ip, instance) in snapshot.items():
            prev = self._state.get(server_id)
            im = metrics_by_instance.get(instance)
            if im is None:
                # Instance отсутствует в ответе — не оцениваем, сохраняем прежний state.
                if prev is not None:
                    new_state[server_id] = prev
                continue
            state, alerts = evaluate(prev, im, name=name, ip=ip)
            new_state[server_id] = state
            for alert in alerts:
                await self._telegram.send_message(alert.text)
        # Серверы вне snapshot выпали из реестра → не попадают в new_state (очистка).
        self._state = new_state

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
