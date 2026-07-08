"""Фоновый монитор доступности бэков (modules/backends, ADR-020, ADR-024).

Отдельная asyncio-задача (по образцу ProxyMonitorService, ADR-019). Состояние
переходов берётся из БД `backends.check_status` (персистентно, переживает рестарт).
Монитор стартует ВСЕГДА; Telegram-отправка гейтится `notifier_enabled` (клиент
передаётся как None при отключённом боте) — `check_status` для UI обновляется
независимо от бота. У бэков НЕТ исхода `unknown`; проверка — прямой `GET https://{domain}/health`.

**ADR-024:** (1) overall-deadline проверки (`asyncio.wait_for`, анти-зависание) →
гарантированно конклюзивный исход; (2) grace-порог `BACKEND_ALERT_AFTER_SEC`:
`check_status→error` немедленно (реальность в UI), но 🔴 шлётся только после
непрерывной недоступности ≥ порога (персистентные `error_since`/`alert_sent`).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.domain.notifications import build_backend_error, build_backend_recovery
from app.infra.backend_check import REASON_TIMEOUT, BackendCheckResult, check_backend
from app.infra.telegram import TelegramClient
from app.logging import get_logger
from app.models.service_backend import Backend, BackendStatus
from app.repositories.backend_repository import BackendRepository

logger = get_logger(__name__)

# Ограничение одновременных проверок бэков за одну итерацию опроса.
_MAX_CONCURRENT_CHECKS = 5

Alert = Literal["error", "recovery"]


@dataclass(frozen=True)
class BackendSnapshot:
    """Снимок бэка для проверки (сессия БД уже закрыта на момент HTTP-запроса).

    Несёт grace-состояние эпизода недоступности (`error_since`/`alert_sent`, ADR-024)
    для time-aware решения об отправке 🔴.
    """

    id: uuid.UUID
    code: str
    name: str
    domain: str
    prev_status: str
    error_since: datetime | None
    alert_sent: bool


@dataclass(frozen=True)
class TransitionResult:
    """Результат чистой функции перехода (ADR-024, time-aware grace-порог)."""

    new_status: str
    error_message: str | None
    new_error_since: datetime | None
    new_alert_sent: bool
    alert: Alert | None


def evaluate_transition(
    prev_status: str,
    result: BackendCheckResult,
    error_since: datetime | None,
    alert_sent: bool,
    now: datetime,
) -> TransitionResult:
    """Чистая функция перехода статуса с grace-порогом (modules/backends, ADR-024).

    Time-aware: 🔴 шлётся только если бэк недоступен непрерывно ≥ `BACKEND_ALERT_AFTER_SEC`
    (`now − error_since`), иначе тихо. `error_since` ставится при `pending|working →
    error`, сбрасывается при `working`. `alert_sent` защищает от повторного 🔴 и гейтит
    recovery-🟢 (шлётся только если 🔴 был). Порог читается из настроек; `now` инъектится
    (тестируется qa без сети/времени). Возвращает `TransitionResult`.
    """
    alert_after_sec = get_settings().backend_alert_after_sec

    if result.outcome == "working":
        # error → working: 🟢 только если 🔴 был отправлен (иначе тихо, напр. рестарт < порога).
        alert: Alert | None = "recovery" if alert_sent else None
        return TransitionResult(
            new_status=BackendStatus.working.value,
            error_message=None,
            new_error_since=None,
            new_alert_sent=False,
            alert=alert,
        )

    # result.outcome == "error"
    if prev_status in (BackendStatus.pending.value, BackendStatus.working.value):
        # Старт нового эпизода недоступности: статус error немедленно, 🔴 позже (grace).
        return TransitionResult(
            new_status=BackendStatus.error.value,
            error_message=result.reason,
            new_error_since=now,
            new_alert_sent=False,
            alert=None,
        )

    # prev_status == error: эпизод продолжается; error_since сохраняется.
    since = error_since if error_since is not None else now
    elapsed = (now - since).total_seconds()
    if not alert_sent and elapsed >= alert_after_sec:
        # Grace-окно истекло → отправить 🔴 (однократно).
        return TransitionResult(
            new_status=BackendStatus.error.value,
            error_message=result.reason,
            new_error_since=since,
            new_alert_sent=True,
            alert="error",
        )
    # Grace-окно ещё не истекло, либо 🔴 уже отправлен → тихо (обновить error_message).
    return TransitionResult(
        new_status=BackendStatus.error.value,
        error_message=result.reason,
        new_error_since=since,
        new_alert_sent=alert_sent,
        alert=None,
    )


class BackendMonitorService:
    """Периодическая проверка бэков + немедленная проверка при создании/edit."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        telegram: TelegramClient | None,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._telegram = telegram
        self._interval_sec = settings.backend_check_interval_sec
        # Overall-deadline одной проверки (анти-зависание, ADR-024).
        self._deadline_sec = settings.backend_check_deadline_sec

    async def check_one(self, backend_id: uuid.UUID) -> None:
        """Проверяет один бэк (немедленная проверка при создании/re-check).

        Загружает снимок из БД, закрывает сессию, проверяет доступность, при
        конклюзивном исходе обновляет БД и при необходимости шлёт алерт.

        Единственная защита исключений для ОБОИХ путей вызова: немедленная проверка
        запускается как fire-and-forget `asyncio.create_task`, поэтому неожиданная
        ошибка не должна всплывать как «Task exception was never retrieved».
        `CancelledError` не глотаем.
        """
        try:
            async with self._sessionmaker() as session:
                repo = BackendRepository(session)
                backend = await repo.get_by_id(backend_id)
                if backend is None:
                    return
                snapshot = self._snapshot(backend)
            await self._check_snapshot(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # проверка не должна валить задачу create_task
            logger.error(
                "backend_check_one_failed",
                backend_id=str(backend_id),
                error_type=type(exc).__name__,
            )

    async def poll_once(self) -> None:
        """Одна итерация: снимок всех бэков → параллельная проверка под семафором."""
        async with self._sessionmaker() as session:
            repo = BackendRepository(session)
            backends = await repo.list_all()
            snapshots = [self._snapshot(backend) for backend in backends]
        # Сессия БД закрыта — далее только HTTP-проверка и короткий UPDATE.

        if not snapshots:
            return

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHECKS)

        async def _guarded(snapshot: BackendSnapshot) -> None:
            async with semaphore:
                await self._check_snapshot(snapshot)

        await asyncio.gather(*(_guarded(snapshot) for snapshot in snapshots))

    @staticmethod
    def _snapshot(backend: Backend) -> BackendSnapshot:
        """Снимок бэка из ORM-объекта (поля читаются в открытой сессии)."""
        return BackendSnapshot(
            id=backend.id,
            code=backend.code,
            name=backend.name,
            domain=backend.domain,
            prev_status=backend.check_status,
            error_since=backend.error_since,
            alert_sent=backend.alert_sent,
        )

    async def _check_snapshot(self, snapshot: BackendSnapshot) -> None:
        """Проверка доступности (с overall-deadline) → обновление БД → grace-алерт."""
        # Overall-deadline (ADR-024): даже если httpx не соблюл per-attempt таймаут,
        # wait_for жёстко прерывает проверку → гарантированно конклюзивный исход.
        try:
            result = await asyncio.wait_for(
                check_backend(snapshot.domain), timeout=self._deadline_sec
            )
        except TimeoutError:
            result = BackendCheckResult("error", REASON_TIMEOUT)

        if result.outcome == "error":
            # Без тел ответов: code/domain/причина (05-security.md).
            logger.warning(
                "backend_check_error",
                backend_id=str(snapshot.id),
                code=snapshot.code,
                domain=snapshot.domain,
                reason=result.reason,
            )

        now = datetime.now(UTC)
        transition = evaluate_transition(
            snapshot.prev_status,
            result,
            snapshot.error_since,
            snapshot.alert_sent,
            now,
        )

        async with self._sessionmaker() as session:
            repo = BackendRepository(session)
            await repo.update_check(
                snapshot.id,
                status=transition.new_status,
                error_message=transition.error_message,
                last_checked_at=now,
                error_since=transition.new_error_since,
                alert_sent=transition.new_alert_sent,
            )
            await session.commit()

        if transition.alert is not None:
            await self._send_alert(transition.alert, snapshot, transition.error_message)

    async def _send_alert(
        self, alert: Alert, snapshot: BackendSnapshot, error_message: str | None
    ) -> None:
        """Отправляет Telegram-алерт, если бот включён; иначе — info-лог (не ошибка)."""
        if self._telegram is None:
            logger.info("backend_alert_suppressed_no_telegram", backend_id=str(snapshot.id))
            return
        if alert == "error":
            text = build_backend_error(
                snapshot.code, snapshot.name, snapshot.domain, error_message or ""
            )
        else:
            text = build_backend_recovery(snapshot.code, snapshot.name, snapshot.domain)
        await self._telegram.send_message(text)

    async def run(self) -> None:
        """Бесконечный цикл: опрос → sleep. Ошибка итерации логируется, цикл живёт."""
        logger.info("backend_monitor_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error("backend_monitor_poll_failed", error_type=type(exc).__name__)
                await asyncio.sleep(self._interval_sec)
        except asyncio.CancelledError:
            logger.info("backend_monitor_stopped")
            raise


__all__ = [
    "Alert",
    "BackendMonitorService",
    "BackendSnapshot",
    "TransitionResult",
    "evaluate_transition",
]
