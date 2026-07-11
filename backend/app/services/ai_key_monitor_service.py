"""Фоновый монитор валидности AI-ключей (modules/ai-keys, ADR-010).

Отдельная asyncio-задача (НЕ state-машина серверного нотификатора). Состояние
переходов берётся из БД `ai_keys.check_status` (персистентно, переживает рестарт).
Монитор стартует ВСЕГДА; Telegram-отправка гейтится `notifier_enabled` (клиент
передаётся как None при отключённом боте) — `check_status` для UI обновляется
независимо от бота.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.notifications import BackendRef, build_key_error, build_key_recovery
from app.infra.ai_provider import KeyCheckResult, check_key
from app.infra.crypto import CryptoError, decrypt_secret
from app.infra.telegram import TelegramClient
from app.logging import get_logger
from app.models.ai_key import AiKeyStatus, AiProvider
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.backend_repository import BackendRepository
from app.services.alert_backend_refs import to_backend_refs

logger = get_logger(__name__)

# Ограничение одновременных проверок провайдеров за одну итерацию опроса.
_MAX_CONCURRENT_CHECKS = 5

Alert = Literal["error", "recovery"]


@dataclass(frozen=True)
class KeySnapshot:
    """Снимок ключа для проверки (сессия БД уже закрыта на момент HTTP-запроса)."""

    id: uuid.UUID
    name: str
    provider: str
    key_encrypted: bytes
    prev_status: str
    key_last4: str | None


def evaluate_transition(
    old_status: str, result: KeyCheckResult
) -> tuple[str, str | None, Alert | None]:
    """Чистая функция перехода статуса (modules/ai-keys#переходы-статуса-и-алерты).

    Возвращает `(new_status, error_message, alert)`, где `alert ∈ {None,'error','recovery'}`.
    Правило:
      - `unknown` → статус НЕ меняется, алерта нет (вызывающий не должен персистить);
      - `pending|working → error` ⇒ alert `'error'`;
      - `error → working` ⇒ alert `'recovery'`;
      - `working|pending → working` и `error → error` ⇒ alert `None` (error_message
        при `error → error` обновляется на актуальную причину).
    Тестируется qa без сети/БД.
    """
    if result.outcome == "unknown":
        return old_status, None, None

    if result.outcome == "working":
        alert: Alert | None = "recovery" if old_status == AiKeyStatus.error.value else None
        return AiKeyStatus.working.value, None, alert

    # result.outcome == "error"
    alert = (
        "error" if old_status in (AiKeyStatus.pending.value, AiKeyStatus.working.value) else None
    )
    return AiKeyStatus.error.value, result.reason, alert


class AiKeyMonitorService:
    """Периодическая проверка ключей + немедленная проверка при создании."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        telegram: TelegramClient | None,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._telegram = telegram
        self._interval_sec = settings.ai_key_check_interval_sec

    async def check_one(self, ai_key_id: uuid.UUID) -> None:
        """Проверяет один ключ (немедленная проверка при создании / ad-hoc).

        Загружает снимок из БД, закрывает сессию, проверяет провайдера, при
        детерминированном исходе обновляет БД и при необходимости шлёт алерт.

        Единственная защита исключений для ОБОИХ путей вызова: немедленная проверка
        запускается как fire-and-forget `asyncio.create_task`, поэтому неожиданная
        ошибка (например сбой сессии БД на UPDATE) не должна всплывать как
        «Task exception was never retrieved». Логируем тип ошибки без ключа/секретов
        (по образцу защиты итерации в `run()`). `CancelledError` не глотаем.
        """
        try:
            async with self._sessionmaker() as session:
                repo = AiKeyRepository(session)
                ai_key = await repo.get_by_id(ai_key_id)
                if ai_key is None:
                    return
                snapshot = KeySnapshot(
                    id=ai_key.id,
                    name=ai_key.name,
                    provider=ai_key.provider,
                    key_encrypted=ai_key.key_encrypted,
                    prev_status=ai_key.check_status,
                    key_last4=ai_key.key_last4,
                )
            await self._check_snapshot(snapshot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # проверка не должна валить фоновую задачу/задачу create_task
            logger.error(
                "ai_key_check_one_failed",
                ai_key_id=str(ai_key_id),
                error_type=type(exc).__name__,
            )

    async def poll_once(self) -> None:
        """Одна итерация: снимок всех ключей → параллельная проверка под семафором."""
        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
            keys = await repo.list_all()
            snapshots = [
                KeySnapshot(
                    id=key.id,
                    name=key.name,
                    provider=key.provider,
                    key_encrypted=key.key_encrypted,
                    prev_status=key.check_status,
                    key_last4=key.key_last4,
                )
                for key in keys
            ]
        # Сессия БД закрыта — далее только расшифровка/HTTP/короткий UPDATE.

        if not snapshots:
            return

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CHECKS)

        async def _guarded(snapshot: KeySnapshot) -> None:
            async with semaphore:
                await self._check_snapshot(snapshot)

        await asyncio.gather(*(_guarded(snapshot) for snapshot in snapshots))

    async def _check_snapshot(self, snapshot: KeySnapshot) -> None:
        """Расшифровка → проверка провайдера → обновление БД → алерт (по переходу)."""
        try:
            api_key = decrypt_secret(snapshot.key_encrypted)
        except CryptoError:
            logger.error("ai_key_decrypt_failed", ai_key_id=str(snapshot.id))
            return

        try:
            provider = AiProvider(snapshot.provider)
        except ValueError:
            logger.error("ai_key_unknown_provider", ai_key_id=str(snapshot.id))
            return

        result = await check_key(provider, api_key)
        if result.outcome == "unknown":
            # Транзиентная недоступность провайдера — статус не флипаем, не алертим.
            logger.warning("ai_key_check_unknown", ai_key_id=str(snapshot.id))
            return

        new_status, error_message, alert = evaluate_transition(snapshot.prev_status, result)

        async with self._sessionmaker() as session:
            repo = AiKeyRepository(session)
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
        self, alert: Alert, snapshot: KeySnapshot, error_message: str | None
    ) -> None:
        """Отправляет Telegram-алерт, если бот включён; иначе — info-лог (не ошибка).

        Алерт «Ключ не работает» дополняется перечнем бэков, использующих этот ключ
        (ADR-046 §1). Перечень резолвится только когда сообщение реально формируется и
        бот включён (при выключенном боте лишний SELECT не делается). Recovery-алерт
        перечнем НЕ расширяется.
        """
        if self._telegram is None:
            logger.info("ai_key_alert_suppressed_no_telegram", ai_key_id=str(snapshot.id))
            return
        if alert == "error":
            backends = await self._backend_refs(snapshot.id)
            text = build_key_error(snapshot.name, snapshot.key_last4, error_message or "", backends)
        else:
            text = build_key_recovery(snapshot.name, snapshot.key_last4)
        await self._telegram.send_message(text)

    async def _backend_refs(self, ai_key_id: uuid.UUID) -> list[BackendRef]:
        """Бэки, использующие ключ (`backends.ai_key_id`), в порядке `position ASC, code ASC`.

        Проекция в `BackendRef` выполняется ВНУТРИ сессии: после выхода из блока
        ORM-объекты detached, и чтение их колонок стало бы латентным
        `DetachedInstanceError` при любом изменении настроек сессии.
        """
        async with self._sessionmaker() as session:
            backends = await BackendRepository(session).list_by_ai_key(ai_key_id)
            return to_backend_refs(backends)

    async def run(self) -> None:
        """Бесконечный цикл: опрос → sleep. Ошибка итерации логируется, цикл живёт."""
        logger.info("ai_key_monitor_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error("ai_key_monitor_poll_failed", error_type=type(exc).__name__)
                await asyncio.sleep(self._interval_sec)
        except asyncio.CancelledError:
            logger.info("ai_key_monitor_stopped")
            raise


__all__ = [
    "AiKeyMonitorService",
    "Alert",
    "KeySnapshot",
    "evaluate_transition",
]
