"""Unit-тесты чистой функции перехода монитора бэков (modules/backends#переходы-статуса).

Матрица `evaluate_transition(old_status, result) -> (new_status, error_message, alert)`
(ADR-020): pending|working→error ⇒ alert 'error'; error→working ⇒ 'recovery';
working→working, pending→working, error→error ⇒ None (при error→error error_message
обновляется на актуальную причину). Исхода `unknown` у бэков НЕТ. Без сети/БД.
"""

from __future__ import annotations

from app.infra.backend_check import (
    REASON_BACKEND_ERROR,
    REASON_TIMEOUT,
    REASON_UNREACHABLE,
    BackendCheckResult,
)
from app.models.service_backend import BackendStatus
from app.services.backend_monitor_service import evaluate_transition

WORKING = BackendCheckResult("working", None)
ERROR_TIMEOUT = BackendCheckResult("error", REASON_TIMEOUT)
ERROR_UNREACHABLE = BackendCheckResult("error", REASON_UNREACHABLE)
ERROR_HTTP = BackendCheckResult("error", "Ошибка бэка (HTTP 503)")
ERROR_GENERIC = BackendCheckResult("error", REASON_BACKEND_ERROR)

PENDING = BackendStatus.pending.value
OK = BackendStatus.working.value
ERR = BackendStatus.error.value


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


def test_working_to_error_http_reason_carries_status_code() -> None:
    new_status, error_message, alert = evaluate_transition(OK, ERROR_HTTP)
    assert new_status == ERR
    assert error_message == "Ошибка бэка (HTTP 503)"
    assert alert == "error"


def test_error_to_error_silent_updates_reason() -> None:
    # Уже сломан → без алерта, но error_message обновляется на актуальную причину.
    new_status, error_message, alert = evaluate_transition(ERR, ERROR_GENERIC)
    assert new_status == ERR
    assert error_message == REASON_BACKEND_ERROR
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
    # Первая успешная проверка — не recovery, молча (modules/backends).
    new_status, error_message, alert = evaluate_transition(PENDING, WORKING)
    assert new_status == OK
    assert error_message is None
    assert alert is None
