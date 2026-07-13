"""Лёгкий аудит через структурированные логи (05-security.md, ADR-035).

Персистентная аудит-таблица действий пользователей остаётся TD-001; на Этапе 1
чувствительные действия (reveal секрета) фиксируются structlog-событием без
значения секрета. Фильтр секретов (`app/logging`) дополнительно маскирует
чувствительные ключи, но само значение секрета сюда не передаётся вовсе.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.logging import get_logger

if TYPE_CHECKING:
    from app.api.deps import Principal

logger = get_logger(__name__)


def log_secret_revealed(principal: Principal, *, resource_type: str, resource_id: str) -> None:
    """Пишет аудит-событие `secret_revealed` при успешном reveal (ADR-035, нормативно).

    Поля: `actor` (username принципала), `user_id` (UUID; у супер-админа — константа
    системной строки-якоря `SUPERADMIN_USER_ID`, ADR-051 §1.2 — принципала без
    идентичности больше не существует), `resource_type` (`server`/`proxy`/`ai_key`/
    `backend`), `resource_id`, `at`. Само значение секрета НЕ передаётся и НЕ логируется.
    """
    logger.info(
        "secret_revealed",
        actor=principal.username,
        user_id=str(principal.user_id),
        resource_type=resource_type,
        resource_id=resource_id,
        at=datetime.now(UTC).isoformat(),
    )
