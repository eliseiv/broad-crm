"""Unit-тесты credit-probe transitions (ADR-075)."""

from __future__ import annotations

from app.infra.ai_provider import CreditProbeResult
from app.services.ai_key_credit_probe_service import evaluate_credit_transition


def test_unknown_keeps_previous() -> None:
    status, err, alert = evaluate_credit_transition("ok", CreditProbeResult("unknown", None))
    assert status == "ok"
    assert err is None
    assert alert is None


def test_first_depleted_alerts() -> None:
    status, err, alert = evaluate_credit_transition(
        None, CreditProbeResult("depleted", "Недостаточно средств")
    )
    assert status == "depleted"
    assert err == "Недостаточно средств"
    assert alert == "depleted"


def test_depleted_to_ok_recovers() -> None:
    status, err, alert = evaluate_credit_transition("depleted", CreditProbeResult("ok", None))
    assert status == "ok"
    assert err is None
    assert alert == "recovered"


def test_ok_stays_silent() -> None:
    status, err, alert = evaluate_credit_transition("ok", CreditProbeResult("ok", None))
    assert status == "ok"
    assert alert is None


def test_depleted_stays_silent() -> None:
    status, err, alert = evaluate_credit_transition(
        "depleted", CreditProbeResult("depleted", "Недостаточно средств")
    )
    assert status == "depleted"
    assert alert is None
