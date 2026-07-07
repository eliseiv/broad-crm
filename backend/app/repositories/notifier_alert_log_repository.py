"""Репозиторий durable-лога алертов нотификатора (SQLAlchemy 2.0 async).

Одна операция (modules/notifier «Durable-лог алертов», ADR-018): вставка строки
на каждый отправленный серверный алерт (`server_id`, `kind`, `message`, `delivered`).
Append-only; коммит выполняет вызывающий сервис в финальной короткой сессии итерации
(вместе с UPSERT состояния). Секретов в `message` нет (токен/chat_id/URL не входят).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notifier_alert_log import NotifierAlertLog


class NotifierAlertLogRepository:
    """Вставка строк `notifier_alert_log` (append-only)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (управление транзакцией — в сервисе)."""
        return self._session

    async def insert(
        self,
        server_id: uuid.UUID | None,
        kind: str,
        message: str,
        delivered: bool,
    ) -> None:
        """Добавляет строку лога отправленного алерта.

        `id`/`created_at` проставляются БД (IDENTITY / `now()`). Коммит — на
        вызывающем сервисе (пишется в той же сессии, что и UPSERT состояния).
        """
        self._session.add(
            NotifierAlertLog(
                server_id=server_id,
                kind=kind,
                message=message,
                delivered=delivered,
            )
        )
