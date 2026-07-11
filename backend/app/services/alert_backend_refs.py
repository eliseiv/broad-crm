"""Перечень бэков для Telegram-алертов об ошибках (ADR-046 §1).

Общий хелпер для серверных алертов (`NotifierService`) и алерта ИИ-ключа
(`AiKeyMonitorService`): строки `backends` → `BackendRef` в **нормативном порядке
`position ASC, code ASC`** (modules/notifier, modules/ai-keys). Репозиторные методы
reverse-lookup (`list_by_server`/`list_by_ai_key`, ADR-040) отдают порядок
`position ASC, created_at DESC, id` — для алертов tie-break переопределяется на `code`,
поэтому сортировка выполняется здесь, а контракт reverse-lookup-эндпоинтов не меняется.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.notifications import BackendRef
from app.models.service_backend import Backend


def to_backend_refs(backends: Sequence[Backend]) -> list[BackendRef]:
    """Строки `backends` → `(code, name, domain)` в порядке `position ASC, code ASC`."""
    ordered = sorted(backends, key=lambda backend: (backend.position, backend.code))
    return [(backend.code, backend.name, backend.domain) for backend in ordered]


__all__ = ["to_backend_refs"]
