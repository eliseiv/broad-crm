"""Unit-тесты чистой функции перехода монитора AI-ключей (modules/ai-keys).

Матрица переходов `evaluate_transition(prev, outcome) -> (new_status, error_message, alert)`:
pending|working→error ⇒ alert 'error'; error→working ⇒ 'recovery'; working/pending→working,
error→error ⇒ None (при error→error обновляется error_message); unknown ⇒ статус НЕ меняется,
алерта нет. Без сети/БД.
"""

from __future__ import annotations

from app.infra.ai_provider import KeyCheckResult
from app.models.ai_key import AiKeyStatus
from app.services.ai_key_monitor_service import evaluate_transition

WORKING = KeyCheckResult("working", None)
ERROR_QUOTA = KeyCheckResult("error", "Недостаточно средств")
ERROR_INVALID = KeyCheckResult("error", "Ключ недействителен")
UNKNOWN = KeyCheckResult("unknown", None)

PENDING = AiKeyStatus.pending.value
OK = AiKeyStatus.working.value
ERR = AiKeyStatus.error.value


# --------------------------------------------------------- переходы в error (🔴)
def test_pending_to_error_alerts() -> None:
    new_status, error_message, alert = evaluate_transition(PENDING, ERROR_QUOTA)
    assert new_status == ERR
    assert error_message == "Недостаточно средств"
    assert alert == "error"


def test_working_to_error_alerts() -> None:
    new_status, error_message, alert = evaluate_transition(OK, ERROR_INVALID)
    assert new_status == ERR
    assert error_message == "Ключ недействителен"
    assert alert == "error"


def test_error_to_error_silent_updates_reason() -> None:
    # Уже сломан → без алерта, но error_message обновляется на актуальную причину.
    new_status, error_message, alert = evaluate_transition(ERR, ERROR_INVALID)
    assert new_status == ERR
    assert error_message == "Ключ недействителен"
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
    # Первая успешная проверка — не recovery, молча.
    new_status, error_message, alert = evaluate_transition(PENDING, WORKING)
    assert new_status == OK
    assert error_message is None
    assert alert is None


# --------------------------------------------------------------- unknown (тишина)
def test_unknown_keeps_status_no_alert_from_pending() -> None:
    new_status, error_message, alert = evaluate_transition(PENDING, UNKNOWN)
    assert new_status == PENDING  # статус НЕ меняется
    assert error_message is None
    assert alert is None


def test_unknown_keeps_status_no_alert_from_working() -> None:
    new_status, error_message, alert = evaluate_transition(OK, UNKNOWN)
    assert new_status == OK
    assert error_message is None
    assert alert is None


def test_unknown_keeps_status_no_alert_from_error() -> None:
    new_status, error_message, alert = evaluate_transition(ERR, UNKNOWN)
    assert new_status == ERR
    assert error_message is None
    assert alert is None
