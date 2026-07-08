"""Фоновый монитор доступности прокси (modules/proxies, ADR-019).

Отдельная asyncio-задача (по образцу AiKeyMonitorService, ADR-010). Состояние
переходов берётся из БД `proxies.check_status` (персистентно, переживает рестарт).
Монитор стартует ВСЕГДА; Telegram-отправка гейтится `notifier_enabled` (клиент
передаётся как None при отключённом боте) — `check_status` для UI обновляется
независимо от бота. У прокси НЕТ исхода `unknown`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.notifications import build_proxy_error, build_proxy_recovery
from app.infra.crypto import CryptoError, decrypt_secret
from app.infra.proxy_check import REASON_TIMEOUT, ProxyCheckResult, check_proxy
from app.infra.telegram import TelegramClient
from app.logging import get_logger
from app.models.proxy import ProxyStatus
from app.repositories.proxy_repository import ProxyRepository

logger = get_logger(__name__)

# Ограничение одновременных проверок прокси за одну итерацию опроса.
_MAX_CONCURRENT_CHECKS = 5

Alert = Literal["error", "recovery"]


@dataclass(frozen=True)
class ProxySnapshot:
    """Снимок прокси для проверки (сессия БД уже закрыта на момент HTTP-запроса)."""

    id: uuid.UUID
    name: str
    proxy_type: str
    host: str
    port: int
    username: str | None
    password_encrypted: bytes | None
    prev_status: str


def evaluate_transition(
    old_status: str, result: ProxyCheckResult
) -> tuple[str, str | None, Alert | None]:
    """Чистая функция перехода статуса (modules/proxies#переходы-статуса-и-алерты).

    Возвращает `(new_status, error_message, alert)`, где `alert ∈ {None,'error','recovery'}`.
    Правило (исхода `unknown` нет):
      - `pending|working → error` ⇒ alert `'error'`;
      - `error → working` ⇒ alert `'recovery'`;
      - `working|pending → working` и `error → error` ⇒ alert `None` (error_message
        при `error → error` обновляется на актуальную причину).
    Тестируется qa без сети/БД.
    """
    if result.outcome == "working":
        alert: Alert | None = "recovery" if old_status == ProxyStatus.error.value else None
        return ProxyStatus.working.value, None, alert

    # result.outcome == "error"
    alert = (
        "error" if old_status in (ProxyStatus.pending.value, ProxyStatus.working.value) else None
    )
    return ProxyStatus.error.value, result.reason, alert


class ProxyMonitorService:
    """Периодическая проверка прокси + немедленная проверка при создании/edit."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        telegram: TelegramClient | None,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._telegram = telegram
        self._interval_sec = settings.proxy_check_interval_sec
        # Overall-deadline одной проверки (анти-зависание, ADR-024): жёсткий верхний
        # предел поверх per-attempt httpx.Timeout и ретраев.
        self._deadline_sec = settings.proxy_check_deadline_sec

    async def check_one(self, proxy_id: uuid.UUID) -> None:
        """Проверяет один прокси (немедленная проверка при создании/re-check).

        Загружает снимок из БД, закрывает сессию, проверяет доступность, при
        конклюзивном исходе обновляет БД и при необходимости шлёт алерт.

        Единственная защита исключений для ОБОИХ путей вызова: немедленная проверка
        запускается как fire-and-forget `asyncio.create_task`, поэтому неожиданная
        ошибка не должна всплывать как «Task exception was never retrieved».
        Логируем тип ошибки без секретов. `CancelledError` не глотаем.
        """
        try:
            async with self._sessionmaker() as session:
                repo = ProxyRepository(session)
                proxy = await repo.get_by_id(proxy_id)
                if proxy is None:
                    return
                snapshot = ProxySnapshot(
                    id=proxy.id,
                    name=proxy.name,
                    proxy_type=proxy.proxy_type,
                    host=proxy.host,
                    port=proxy.port,
                    username=proxy.username,
                    password_encrypted=proxy.password_encrypted,
                    prev_status=proxy.check_status,
                )
            await self._check_snapshot(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # проверка не должна валить задачу create_task
            logger.error(
                "proxy_check_one_failed",
                proxy_id=str(proxy_id),
                error_type=type(exc).__name__,
            )

    async def poll_once(self) -> None:
        """Одна итерация: снимок всех прокси → параллельная проверка под семафором."""
        async with self._sessionmaker() as session:
            repo = ProxyRepository(session)
            proxies = await repo.list_all()
            snapshots = [
                ProxySnapshot(
                    id=proxy.id,
                    name=proxy.name,
                    proxy_type=proxy.proxy_type,
                    host=proxy.host,
                    port=proxy.port,
                    username=proxy.username,
                    password_encrypted=proxy.password_encrypted,
                    prev_status=proxy.check_status,
                )
                for proxy in proxies
            ]
        # Сессия БД закрыта — далее только расшифровка/HTTP/короткий UPDATE.

        if not snapshots:
            return

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHECKS)

        async def _guarded(snapshot: ProxySnapshot) -> None:
            async with semaphore:
                await self._check_snapshot(snapshot)

        await asyncio.gather(*(_guarded(snapshot) for snapshot in snapshots))

    async def _check_snapshot(self, snapshot: ProxySnapshot) -> None:
        """Расшифровка пароля → проверка доступности → обновление БД → алерт."""
        password: str | None = None
        if snapshot.password_encrypted is not None:
            try:
                password = decrypt_secret(snapshot.password_encrypted)
            except CryptoError:
                logger.error("proxy_decrypt_failed", proxy_id=str(snapshot.id))
                return

        # Overall-deadline (ADR-024): даже если httpx/socksio не соблюли per-attempt
        # таймаут, wait_for жёстко прерывает проверку → гарантированно конклюзивный исход.
        try:
            result = await asyncio.wait_for(
                check_proxy(
                    snapshot.proxy_type,
                    snapshot.host,
                    snapshot.port,
                    snapshot.username,
                    password,
                ),
                timeout=self._deadline_sec,
            )
        except TimeoutError:
            result = ProxyCheckResult("error", REASON_TIMEOUT)
        if result.outcome == "error":
            # Без секретов: только id и причина (URL/пароль не логируются).
            logger.warning("proxy_check_error", proxy_id=str(snapshot.id), reason=result.reason)

        new_status, error_message, alert = evaluate_transition(snapshot.prev_status, result)

        async with self._sessionmaker() as session:
            repo = ProxyRepository(session)
            await repo.update_check(
                snapshot.id,
                status=new_status,
                error_message=error_message,
                last_checked_at=datetime.now(UTC),
            )
            await session.commit()

        if alert is not None:
            await self._send_alert(alert, snapshot, error_message)

    async def _send_alert(
        self, alert: Alert, snapshot: ProxySnapshot, error_message: str | None
    ) -> None:
        """Отправляет Telegram-алерт, если бот включён; иначе — info-лог (не ошибка)."""
        if self._telegram is None:
            logger.info("proxy_alert_suppressed_no_telegram", proxy_id=str(snapshot.id))
            return
        if alert == "error":
            text = build_proxy_error(
                snapshot.name, snapshot.host, snapshot.port, error_message or ""
            )
        else:
            text = build_proxy_recovery(snapshot.name, snapshot.host, snapshot.port)
        await self._telegram.send_message(text)

    async def run(self) -> None:
        """Бесконечный цикл: опрос → sleep. Ошибка итерации логируется, цикл живёт."""
        logger.info("proxy_monitor_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error("proxy_monitor_poll_failed", error_type=type(exc).__name__)
                await asyncio.sleep(self._interval_sec)
        except asyncio.CancelledError:
            logger.info("proxy_monitor_stopped")
            raise


__all__ = [
    "Alert",
    "ProxyMonitorService",
    "ProxySnapshot",
    "evaluate_transition",
]
