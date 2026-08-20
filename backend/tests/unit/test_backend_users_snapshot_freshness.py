"""Регресс-гейт метки свежести снимка «Юзеров бэков» (прод-инцидент 2026-08-20).

**Что сломалось на проде.** `_snapshot_at` возвращал `None`, если не собран хоть ОДИН
источник. Три бэка не могли собраться никогда — у двух отвергнут admin-ключ, третий не
отдаёт контракт v1 — и из-за них страница показывала «Снимок формируется…» поверх
68 262 уже собранных строк, бессрочно. Кэширование при этом работало: 20 из 30
источников обновлялись каждый цикл.

Метка отвечает на вопрос «насколько стары ПОКАЗАННЫЕ строки», а показаны строки
собранных источников. Несобранные видны оператору отдельно — в `errors[]`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.repositories.backend_user_snapshot_repository import SnapshotSourceState
from app.services.backend_user_service import _snapshot_at

BACKEND_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
BACKEND_B = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
BACKEND_C = uuid.UUID("00000000-0000-0000-0000-0000000000a3")

OLDER = datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
NEWER = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _state(backend_id: uuid.UUID, refreshed_at: datetime | None) -> SnapshotSourceState:
    return SnapshotSourceState(
        backend_id=backend_id,
        refreshed_at=refreshed_at,
        error_message=None if refreshed_at else "Ошибка бэка (HTTP 500)",
        stats_users_total=0,
        stats_paid_users=0,
        stats_payments_sum_usd=0.0,
        api_costs={},
        revenue_backfill_done=True,
        revenue_supported=True,
    )


def test_broken_source_does_not_hide_collected_data() -> None:
    """⛔ Главный гейт инцидента: несобранный источник НЕ гасит метку остальных."""
    states = [_state(BACKEND_A, NEWER), _state(BACKEND_B, None)]

    assert _snapshot_at([BACKEND_A, BACKEND_B], states) == NEWER


def test_returns_oldest_of_collected_sources() -> None:
    """Из показанных данных честна самая старая метка — `MIN`, а не `MAX`."""
    states = [_state(BACKEND_A, NEWER), _state(BACKEND_B, OLDER), _state(BACKEND_C, None)]

    assert _snapshot_at([BACKEND_A, BACKEND_B, BACKEND_C], states) == OLDER


def test_none_only_when_nothing_collected_at_all() -> None:
    """`None` (и «Снимок формируется…») остаётся ровно для случая «данных нет вовсе»."""
    assert _snapshot_at([BACKEND_A, BACKEND_B], [_state(BACKEND_A, None)]) is None
    assert _snapshot_at([BACKEND_A], []) is None


def test_source_without_state_row_is_ignored_not_fatal() -> None:
    """Бэк, до которого воркер ещё не дошёл, не обнуляет метку уже собранных."""
    states = [_state(BACKEND_A, NEWER)]

    assert _snapshot_at([BACKEND_A, BACKEND_B], states) == NEWER
