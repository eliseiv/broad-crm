"""Схемы реестра серверов (04-api.md#servers)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, IPvAnyAddress

from app.models.server import ProvisionStatus
from app.schemas.metrics import Metric, ServerMetrics


class ServerCreateRequest(BaseModel):
    """Тело POST /api/servers."""

    name: str = Field(min_length=1, max_length=64)
    ip: IPvAnyAddress
    ssh_user: str = Field(min_length=1, max_length=64)
    ssh_password: str = Field(min_length=1, max_length=256)


class ServerUpdateRequest(BaseModel):
    """Тело PATCH /api/servers/{id} — на Этапе 1 меняется только `name`."""

    name: str = Field(min_length=1, max_length=64)


class ServerOrderRequest(BaseModel):
    """Тело PATCH /api/servers/order — полная перестановка множества серверов."""

    ids: list[uuid.UUID]


class ServerCreatedResponse(BaseModel):
    """Ответ 202 POST /api/servers (без пароля)."""

    id: uuid.UUID
    name: str
    ip: str
    exporter_port: int
    provision_status: ProvisionStatus
    position: int


class ServerSummaryResponse(BaseModel):
    """Ответ 200 PATCH /api/servers/{id} — summary-объект сервера (без метрик)."""

    id: uuid.UUID
    name: str
    ip: str
    exporter_port: int
    provision_status: ProvisionStatus
    position: int
    created_at: datetime
    updated_at: datetime


class ServerListItem(BaseModel):
    """Элемент списка GET /api/servers с метриками и статусом."""

    id: uuid.UUID
    name: str
    ip: str
    exporter_port: int
    provision_status: ProvisionStatus
    position: int
    online: bool
    uptime_seconds: int | None
    last_updated: datetime | None
    metrics: ServerMetrics | None


class ServerListResponse(BaseModel):
    """Ответ 200 GET /api/servers."""

    items: list[ServerListItem]


class ServerMetricsResponse(BaseModel):
    """Ответ 200 GET /api/servers/{id}/metrics."""

    id: uuid.UUID
    online: bool
    uptime_seconds: int | None
    last_updated: datetime | None
    cpu: Metric
    ram: Metric
    ssd: Metric


class ServerStatusResponse(BaseModel):
    """Ответ 200 GET /api/servers/{id}/status."""

    id: uuid.UUID
    provision_status: ProvisionStatus
    error_message: str | None
    updated_at: datetime
