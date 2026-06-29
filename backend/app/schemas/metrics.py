"""Схемы метрик карточки сервера (04-api.md#схема-объекта-метрики-и-detail)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Zone = Literal["green", "yellow", "red"]


class MetricDetail(BaseModel):
    """Абсолютные значения метрики. value/total могут быть null (см. CPU fallback)."""

    value: float | None
    total: float | None
    unit: str


class Metric(BaseModel):
    """Одна метрика: процент загрузки, зона и абсолютные значения.

    Когда метрика реально не получена (online=false / up==0 / отсутствует в ответе
    Prometheus) — usage_percent и zone = null (04-api.md «Доступность метрик»):
    ложные/нулевые значения не подставляются.
    """

    usage_percent: float | None
    zone: Zone | None
    detail: MetricDetail


class ServerMetrics(BaseModel):
    """Тройка метрик CPU/RAM/SSD карточки сервера."""

    cpu: Metric
    ram: Metric
    ssd: Metric
