"""Unit-тесты чистой функции перехода монитора прокси (modules/proxies).

Матрица переходов `evaluate_transition(old_status, result) -> (new_status,
error_message, alert)` (ADR-019, modules/proxies#переходы-статуса-и-алерты):
pending|working→error ⇒ alert 'error'; error→working ⇒ 'recovery'; working→working,
pending→working, error→error ⇒ None (при error→error error_message обновляется на
актуальную причину). Исхода `unknown` у прокси НЕТ. Без сети/БД.
"""

from __future__ import annotations

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


# --------------------------------------------------------- переходы в error (🔴)
def test_pending_to_error_alerts() -> None:
    new_status, error_message, alert = evaluate_transition(PENDING, ERROR_TIMEOUT)
    assert new_status == ERR
    assert error_message == REASON_TIMEOUT
    assert alert == "error"


def test_working_to_error_alerts() -> None:
    new_status, error_message, alert = evaluate_transition(OK, ERROR_UNREACHABLE)
    assert new_status == ERR
    assert error_message == REASON_UNREACHABLE
    assert alert == "error"


def test_error_to_error_silent_updates_reason() -> None:
    # Уже сломан → без алерта, но error_message обновляется на актуальную причину.
    new_status, error_message, alert = evaluate_transition(ERR, ERROR_PROXY)
    assert new_status == ERR
    assert error_message == REASON_PROXY_ERROR
    assert alert is None


# ------------------------------------------------------ переходы в working (🟢)
def test_error_to_working_recovery_alert() -> None:
    new_status, error_message, alert = evaluate_transition(ERR, WORKING)
    assert new_status == OK
    assert error_message is None
    assert alert == "recovery"


def test_working_to_working_silent() -> None:
    new_status, error_message, alert = evaluate_transition(OK, WORKING)
    assert new_status == OK
    assert error_message is None
    assert alert is None


def test_pending_to_working_silent_not_recovery() -> None:
    # Первая успешная проверка — не recovery, молча (modules/proxies).
    new_status, error_message, alert = evaluate_transition(PENDING, WORKING)
    assert new_status == OK
    assert error_message is None
    assert alert is None
