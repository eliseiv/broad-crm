"""Единый модуль порогов зон нагрузки (04-api.md#пороги-зон).

Та же логика дублируется на frontend для оптимистичной отрисовки.
Границы: <80 → green; >=80 и <=90 → yellow; >90 → red.
"""

from __future__ import annotations

from typing import Literal

Zone = Literal["green", "yellow", "red"]

YELLOW_THRESHOLD = 80.0
RED_THRESHOLD = 90.0


def usage_to_zone(usage_percent: float) -> Zone:
    """Возвращает зону по проценту загрузки (границы строго по 04-api.md)."""
    if usage_percent > RED_THRESHOLD:
        return "red"
    if usage_percent >= YELLOW_THRESHOLD:
        return "yellow"
    return "green"
