"""Unit-тесты чистой функции перехода монитора прокси с grace-порогом (ADR-027).

`evaluate_transition(prev_status, result, error_since, alert_sent, now) -> TransitionResult`.
Grace (ADR-027, перенос модели бэков ADR-024): статус переходит в `error` немедленно
(реальность в UI), но 🔴 шлётся ТОЛЬКО после непрерывной недоступности ≥
`PROXY_ALERT_AFTER_SEC` (default 1800 с). При восстановлении 🟢 (recovery) шлётся только
если 🔴 был отправлен (`alert_sent`). Время инъектируется (`now`) — без сети/БД/реального
таймера. Порог читается из `get_settings().proxy_alert_after_sec`. Исхода `unknown` у
прокси НЕТ; матрица идентична бэковой.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.infra.proxy_check import (
    REASON_PROXY_ERROR,
    REASON_TIMEOUT,
    REASON_UNREACHABLE,
    ProxyCheckResult,
)
from app.models.proxy import ProxyStatus
from app.services.proxy_monitor_service import evaluate_transition

WORKING = ProxyCheckResult("working", None)
ERROR_TIMEOUT = ProxyCheckResult("error", REASON_TIMEOUT)
ERROR_UNREACHABLE = ProxyCheckResult("error", REASON_UNREACHABLE)
ERROR_PROXY = ProxyCheckResult("error", REASON_PROXY_ERROR)

PENDING = ProxyStatus.pending.value
OK = ProxyStatus.working.value
ERR = ProxyStatus.error.value

_NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=UTC)
# Порог по умолчанию (config.proxy_alert_after_sec) — 30 минут.
_PAST_31_MIN = _NOW - timedelta(minutes=31)
_PAST_10_MIN = _NOW - timedelta(minutes=10)


# ------------------------------------------------ старт эпизода недоступности (grace)
def test_working_to_error_starts_grace_no_alert() -> None:
    t = evaluate_transition(OK, ERROR_UNREACHABLE, None, False, _NOW)
    assert t.new_status == ERR
    assert t.error_message == REASON_UNREACHABLE
    assert t.new_error_since == _NOW  # начало эпизода
    assert t.new_alert_sent is False
    assert t.alert is None  # grace-окно ещё не истекло → тихо


def test_pending_to_error_starts_grace_no_alert() -> None:
    t = evaluate_transition(PENDING, ERROR_TIMEOUT, None, False, _NOW)
    assert t.new_status == ERR
    assert t.error_message == REASON_TIMEOUT
    assert t.new_error_since == _NOW
    assert t.new_alert_sent is False
    assert t.alert is None


# ---------------------------------------------------- эпизод продолжается (error→error)
def test_error_to_error_within_grace_is_silent() -> None:
    # Недоступен всего 10 минут (< 30) → всё ещё тихо, error_since сохраняется,
    # error_message обновляется на актуальную причину.
    t = evaluate_transition(ERR, ERROR_PROXY, _PAST_10_MIN, False, _NOW)
    assert t.new_status == ERR
    assert t.error_message == REASON_PROXY_ERROR
    assert t.new_error_since == _PAST_10_MIN
    assert t.new_alert_sent is False
    assert t.alert is None


def test_error_to_error_after_grace_sends_red_once() -> None:
    # Непрерывно недоступен 31 минуту (≥ 30) и 🔴 ещё не слали → отправить 🔴.
    t = evaluate_transition(ERR, ERROR_UNREACHABLE, _PAST_31_MIN, False, _NOW)
    assert t.new_status == ERR
    assert t.new_error_since == _PAST_31_MIN  # отсчёт не сбрасывается
    assert t.new_alert_sent is True
    assert t.alert == "error"


def test_error_to_error_after_grace_but_alert_already_sent_is_silent() -> None:
    # Порог истёк, но 🔴 уже отправлен → повторно не шлём (защита от дубля).
    t = evaluate_transition(ERR, ERROR_PROXY, _PAST_31_MIN, True, _NOW)
    assert t.new_status == ERR
    assert t.new_alert_sent is True
    assert t.alert is None


def test_error_to_error_missing_error_since_seeds_now() -> None:
    # Персистентность отсутствует (error_since=None при prev=error, напр. после апгрейда) →
    # отсчёт стартует с now (тихо), дубля не будет.
    t = evaluate_transition(ERR, ERROR_TIMEOUT, None, False, _NOW)
    assert t.new_error_since == _NOW
    assert t.new_alert_sent is False
    assert t.alert is None


# ------------------------------------------------------ восстановление (error→working)
def test_error_to_working_recovery_only_if_alert_sent() -> None:
    t = evaluate_transition(ERR, WORKING, _PAST_31_MIN, True, _NOW)
    assert t.new_status == OK
    assert t.error_message is None
    assert t.new_error_since is None  # эпизод закрыт
    assert t.new_alert_sent is False
    assert t.alert == "recovery"


def test_error_to_working_no_recovery_if_alert_not_sent() -> None:
    # Флап < 30 мин: 🔴 не слали → 🟢 тоже не нужен (тихо).
    t = evaluate_transition(ERR, WORKING, _PAST_10_MIN, False, _NOW)
    assert t.new_status == OK
    assert t.new_error_since is None
    assert t.new_alert_sent is False
    assert t.alert is None


def test_working_to_working_silent() -> None:
    t = evaluate_transition(OK, WORKING, None, False, _NOW)
    assert t.new_status == OK
    assert t.error_message is None
    assert t.new_error_since is None
    assert t.new_alert_sent is False
    assert t.alert is None


def test_pending_to_working_silent_not_recovery() -> None:
    # Первая успешная проверка — не recovery, молча (modules/proxies).
    t = evaluate_transition(PENDING, WORKING, None, False, _NOW)
    assert t.new_status == OK
    assert t.alert is None
