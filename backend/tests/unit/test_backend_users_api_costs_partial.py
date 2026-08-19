"""Регресс-гейт признака `api_costs.partial` (ADR-080 §5, 06-testing-strategy.md стр. 211).

Нормативный предикат (полная форма, ADR-080 §5):

```
partial = ∃ участвующий источник:  revenue_backfill_done = false
                                   OR revenue_supported IS FALSE
          OR ∃ запрошенный бэк без строки состояния (backend_user_snapshot_sources)

participating == []  ⇒  api_costs = null   (снимок ещё не сформирован)
```

**Кейс, ради которого гейт и написан** — бэк уровня **v1 без блока `revenue`** с
**ЗАВЕРШЁННЫМ** backfill'ом: карточка такого бэка получает `revenue_refreshed_at` при
доборе и штатно покидает очередь, поэтому `revenue_backfill_done` честно становится
`true`, а сумма расходов занижена НАВСЕГДА. Реализация «`partial` = только
`revenue_backfill_done = false`» объявила бы такую сумму полной — и оператор читал бы ноль
расходов как факт. Второй дизъюнкт (`revenue_supported IS FALSE`) закрывает ровно это.

**Третий дизъюнкт** относится не к участвующему источнику, а к отсутствующему:
запрошенный бэк **без строки** в `backend_user_snapshot_sources` (воркер до него ещё не
доходил — бэк только что подключён либо первый цикл не завершён) в сумму не входит вовсе
⇒ `partial`. Ошибкой это состояние не является (`errors[]` пуст — там только сбои, §1),
поэтому единственный сигнал неполноты — сам `partial`.

Вырожденный край того же дизъюнкта: строки нет НИ У ОДНОГО запрошенного бэка ⇒
`api_costs: null` («снимок ещё не сформирован», тот же случай, что `snapshot_at: null`,
§6), а **не** нули с `partial=true`. Разница смысловая: `null` говорит «не собрано
ничего», нули утверждали бы, что расходы ИЗМЕРЕНЫ и равны нулю.

Проверяется чистая функция агрегации (`_api_costs`) — БД и HTTP здесь не при чём:
`partial` выводится ИСКЛЮЧИТЕЛЬНО из строк источников (`O(число бэков)`, сканировать
`backend_user_snapshots` ради того же вывода запрещено, ADR-080 §5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.repositories.backend_user_snapshot_repository import SnapshotSourceState
from app.services.backend_user_service import _api_costs

BACKEND_A = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
BACKEND_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")


def _state(
    backend_id: uuid.UUID,
    *,
    backfill_done: bool,
    revenue_supported: bool | None,
    api_costs: dict[str, float] | None = None,
) -> SnapshotSourceState:
    """Строка источника с собранным снимком (сбоя нет — проверяется только полнота)."""
    return SnapshotSourceState(
        backend_id=backend_id,
        refreshed_at=datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
        error_message=None,
        stats_users_total=0,
        stats_paid_users=0,
        stats_payments_sum_usd=0.0,
        api_costs=api_costs if api_costs is not None else {"openai": 1.5},
        revenue_backfill_done=backfill_done,
        revenue_supported=revenue_supported,
    )


def test_partial_stays_true_for_v1_source_with_finished_backfill() -> None:
    """⛔ Гейт: backfill ЗАВЕРШЁН, но источник не отдаёт `revenue` ⇒ `partial` остаётся `true`.

    Ровно тот случай, где неполнота не исправится сама: очередь пуста
    (`revenue_backfill_done = true`, все `revenue_refreshed_at` проставлены), а блока
    `revenue` у бэка нет и не будет до внедрения v1.1.
    """
    states = [_state(BACKEND_A, backfill_done=True, revenue_supported=False)]

    costs = _api_costs([BACKEND_A], states)

    assert costs is not None
    assert costs.partial is True
    # Сумма при этом отдаётся (её просто нельзя читать как полную).
    assert costs.openai_usd == 1.5


def test_partial_true_while_backfill_is_unfinished() -> None:
    """Незавершённый backfill ⇒ `partial` (первый дизъюнкт), даже если `revenue` поддержан."""
    states = [_state(BACKEND_A, backfill_done=False, revenue_supported=True)]

    costs = _api_costs([BACKEND_A], states)

    assert costs is not None
    assert costs.partial is True


def test_partial_false_when_every_source_supports_revenue_and_backfill_is_done() -> None:
    """Все источники отдают `revenue` и добраны ⇒ сумма полная (`partial = false`).

    Негативная половина гейта: без неё «`partial` всегда `true`» тоже проходило бы.
    """
    states = [
        _state(BACKEND_A, backfill_done=True, revenue_supported=True, api_costs={"openai": 1.0}),
        _state(BACKEND_B, backfill_done=True, revenue_supported=True, api_costs={"fal": 2.0}),
    ]

    costs = _api_costs([BACKEND_A, BACKEND_B], states)

    assert costs is not None
    assert costs.partial is False
    assert costs.total_usd == 3.0


# --- Третий дизъюнкт: источник, которого в снимке ЕЩЁ НЕТ ---------------------
#
# `len(participating) < len(backend_ids)` — запрошенный бэк без строки в
# `backend_user_snapshot_sources`: воркер до него ещё не доходил (только что подключён,
# первый цикл не завершён). Такой источник в сумму не входит вовсе, и объявить её полной
# нельзя — иначе оператор прочитал бы расходы одного бэка как расходы всех выбранных.
# Ошибкой это состояние НЕ является (`errors[]` пуст — ADR-080 §1: там только сбои),
# поэтому единственный сигнал неполноты — сам `partial`.


def test_partial_true_when_a_requested_backend_has_no_source_row() -> None:
    """⛔ Гейт: запрошено два бэка, строка состояния есть только у одного ⇒ `partial`.

    Оба «полных» признака у присутствующего источника в порядке (backfill завершён,
    `revenue` поддержан) — то есть без третьего дизъюнкта предикат дал бы `false`
    и занижённая сумма ушла бы в UI как полная.
    """
    states = [
        _state(BACKEND_A, backfill_done=True, revenue_supported=True, api_costs={"openai": 1.0})
    ]

    costs = _api_costs([BACKEND_A, BACKEND_B], states)

    assert costs is not None
    assert costs.partial is True
    # Сумма считается по тому, что есть, — но читать её как полную нельзя.
    assert costs.total_usd == 1.0


def test_api_costs_is_none_when_no_requested_backend_has_a_source_row() -> None:
    """⛔ Вырожденный случай: ни одной строки состояния ⇒ `None`, а НЕ нули с `partial`.

    `api_costs: null` означает «снимок ещё не сформирован» (ADR-080 §6, тот же случай, что
    `snapshot_at: null`) — UI показывает «Снимок формируется…». Нули с `partial=true`
    были бы враньём другого рода: они утверждают, что расходы ИЗМЕРЕНЫ и равны нулю,
    тогда как не собрано ничего.
    """
    assert _api_costs([BACKEND_A, BACKEND_B], []) is None
    # Строки состояния есть, но у ДРУГИХ бэков (не запрошенных) — пересечение пусто.
    assert (
        _api_costs([BACKEND_A], [_state(BACKEND_B, backfill_done=True, revenue_supported=True)])
        is None
    )
