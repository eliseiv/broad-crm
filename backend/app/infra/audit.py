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
    `ai_key_billing_admin`/`backend`), `resource_id`, `at`. Само значение секрета НЕ
    передаётся и НЕ логируется. У ИИ-ключа два секрета различаются в аудите (`ai_key` —
    inference-ключ, `ai_key_billing_admin` — Admin API key контура остатка, ADR-070
    §Амендмент); у бэка оба секрета пишутся одним `resource_type="backend"` (ADR-040).
    """
    logger.info(
        "secret_revealed",
        actor=principal.username,
        user_id=str(principal.user_id),
        resource_type=resource_type,
        resource_id=resource_id,
        at=datetime.now(UTC).isoformat(),
    )


def log_backend_admin_action(
    principal: Principal,
    *,
    action: str,
    backend_id: str,
    target_user_id: str | None = None,
    detail: str,
) -> None:
    """Аудит admin-операции над бэком (modules/backend-users, modules/backend-economics).

    `action` — `tokens_added` / `subscription_granted` (операция над пользователем,
    ADR-069 §5) либо `product_tokens_updated` / `pricing_updated` (правка каталога,
    ADR-072 §10). `target_user_id` ОПЦИОНАЛЕН: у правки каталога субъекта-пользователя
    нет (ADR-072 §10). `detail` — публичные параметры операции (сумма / product_id +
    дни / дельта `1000->1500`), без секретов. Пишется ПОСЛЕ успешного ответа бэка —
    неуспешная операция события не порождает.
    """
    logger.info(
        "backend_admin_action",
        actor=principal.username,
        user_id=str(principal.user_id),
        action=action,
        backend_id=backend_id,
        target_user_id=target_user_id,
        detail=detail,
        at=datetime.now(UTC).isoformat(),
    )
