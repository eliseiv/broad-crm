"""Unit-тесты клампа окна нотификатора в конфиге (ADR-016, config.py).

NOTIFIER_METRIC_WINDOW_SEC нормативно >= NOTIFIER_POLL_INTERVAL_SEC: окно меньше
интервала опроса оставляет «слепые» зазоры между окнами. config при старте поднимает
эффективное окно до poll_interval и пишет warning-лог notifier_metric_window_clamped;
окно >= интервала используется как есть без клампа и без лога. Нормативный источник —
ADR-016#решение (выбор окна), docs/07-deployment.md#переменные-окружения.
"""

from __future__ import annotations

import structlog
from app.config import Settings

_CLAMP_EVENT = "notifier_metric_window_clamped"


def test_window_below_poll_interval_is_clamped_up_with_warning() -> None:
    with structlog.testing.capture_logs() as logs:
        settings = Settings(notifier_poll_interval_sec=60, notifier_metric_window_sec=30)

    # Эффективное окно поднято до poll_interval (устраняет «слепые» зазоры).
    assert settings.notifier_metric_window_effective_sec == 60
    clamp_events = [event for event in logs if event.get("event") == _CLAMP_EVENT]
    assert len(clamp_events) == 1
    assert clamp_events[0]["configured_sec"] == 30
    assert clamp_events[0]["poll_interval_sec"] == 60
    assert clamp_events[0]["effective_sec"] == 60


def test_window_at_or_above_poll_interval_is_not_clamped() -> None:
    with structlog.testing.capture_logs() as logs:
        settings = Settings(notifier_poll_interval_sec=60, notifier_metric_window_sec=120)

    # Окно >= интервала используется как есть, без клампа и без warning-лога.
    assert settings.notifier_metric_window_effective_sec == 120
    assert not [event for event in logs if event.get("event") == _CLAMP_EVENT]


def test_default_window_equals_poll_interval_not_clamped() -> None:
    # Граница: окно == poll_interval → не клампится (условие клампа строгое <).
    with structlog.testing.capture_logs() as logs:
        settings = Settings(notifier_poll_interval_sec=90, notifier_metric_window_sec=90)

    assert settings.notifier_metric_window_effective_sec == 90
    assert not [event for event in logs if event.get("event") == _CLAMP_EVENT]
