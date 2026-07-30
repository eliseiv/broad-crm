"""Unit-тесты оценочного остатка AI-ключей (ADR-070)."""

from __future__ import annotations

from decimal import Decimal

from app.infra.ai_provider_billing import compute_alert_level, default_low_threshold_usd
from app.services.ai_key_balance_sync_service import evaluate_balance_alert_transition


def test_compute_alert_level_depleted() -> None:
    assert compute_alert_level(Decimal("0"), Decimal("10")) == "depleted"
    assert compute_alert_level(Decimal("-1"), Decimal("10")) == "depleted"


def test_compute_alert_level_low() -> None:
    assert compute_alert_level(Decimal("5"), Decimal("10")) == "low"


def test_compute_alert_level_normal() -> None:
    assert compute_alert_level(Decimal("50"), Decimal("10")) == "normal"


def test_default_threshold() -> None:
    assert default_low_threshold_usd() == Decimal("10.0000")


def test_balance_alert_transition_low() -> None:
    assert evaluate_balance_alert_transition("normal", "low", sync_failed_alert=False) == "low"


def test_balance_alert_transition_depleted() -> None:
    result = evaluate_balance_alert_transition("normal", "depleted", sync_failed_alert=False)
    assert result == "depleted"


def test_balance_alert_transition_recovered() -> None:
    result = evaluate_balance_alert_transition("low", "normal", sync_failed_alert=False)
    assert result == "recovered"


def test_balance_alert_sync_failed() -> None:
    result = evaluate_balance_alert_transition("normal", "normal", sync_failed_alert=True)
    assert result == "sync_failed"
