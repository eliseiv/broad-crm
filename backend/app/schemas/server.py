"""Схемы реестра серверов (04-api.md#servers)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, IPvAnyAddress

from app.models.server import ProvisionStatus, ServerAuthMethod
from app.schemas.metrics import Metric, ServerMetrics


class ServerCreateRequest(BaseModel):
    """Тело POST /api/servers — **дискриминированное** по `auth_method` (ADR-067 §3).

    `auth_method` опционален с дефолтом `password` ⇒ прежнее тело
    `{name, ip, ssh_user, ssh_password}` остаётся валидным (ломающего изменения нет).

    Поля материала объявлены опциональными НАМЕРЕННО: правило «ровно один способ»
    (лишнее поле «чужого» режима — даже `null`/`""` — и отсутствующее обязательное поле)
    проверяет сервис по `model_fields_set`, потому что контракт требует `422
    validation_error` с точным `details[].field`, а pydantic на required-поле дал бы `400`.
    """

    name: str = Field(min_length=1, max_length=64)
    ip: IPvAnyAddress
    ssh_user: str = Field(min_length=1, max_length=64)
    auth_method: ServerAuthMethod = ServerAuthMethod.password
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    ssh_key_passphrase: str | None = None


class ServerUpdateRequest(BaseModel):
    """Тело PATCH /api/servers/{id} — на Этапе 1 меняется только `name`."""

    name: str = Field(min_length=1, max_length=64)


class ServerOrderRequest(BaseModel):
    """Тело PATCH /api/servers/order — полная перестановка множества серверов."""

    ids: list[uuid.UUID]


class ServerCreatedResponse(BaseModel):
    """Ответ 202 POST /api/servers (без материала входа). `ssh_user` — не секрет (ADR-035).

    `auth_method` — способ входа, а не материал (ADR-067 §3); флагов `has_password`/
    `has_key` не вводится: CHECK `ck_servers_auth_material` делает наличие материала
    однозначной функцией `auth_method`.
    """

    id: uuid.UUID
    name: str
    ip: str
    ssh_user: str
    auth_method: ServerAuthMethod
    exporter_port: int
    provision_status: ProvisionStatus
    position: int


class ServerSummaryResponse(BaseModel):
    """Ответ 200 PATCH /api/servers/{id} — summary-объект сервера (без метрик).

    `ssh_user` — SSH-логин целевого сервера (не секрет, ADR-035); SSH-пароль в
    ответе не отдаётся (только через reveal-эндпоинт). `auth_method` — способ входа
    (не секрет, ADR-067).
    """

    id: uuid.UUID
    name: str
    ip: str
    ssh_user: str
    auth_method: ServerAuthMethod
    exporter_port: int
    provision_status: ProvisionStatus
    position: int
    created_at: datetime
    updated_at: datetime


class ServerListItem(BaseModel):
    """Элемент списка GET /api/servers с метриками и статусом.

    `ssh_user` — SSH-логин (не секрет, ADR-035); отображается в read-only
    detail-view сервера. SSH-пароль здесь не отдаётся (только reveal-эндпоинт).
    `auth_method` (ADR-067) нужен UI, чтобы показать строку «Способ входа» и решить,
    рендерить ли кнопку-глаз reveal (у key-сервера её нет ни при каком праве).
    """

    id: uuid.UUID
    name: str
    ip: str
    ssh_user: str
    auth_method: ServerAuthMethod
    exporter_port: int
    provision_status: ProvisionStatus
    position: int
    online: bool
    uptime_seconds: int | None
    last_updated: datetime | None
    metrics: ServerMetrics | None
    # Число бэков, связанных с сервером (COUNT по backends.server_id, ADR-040) — для
    # свёрнутой секции «Бэки» в detail-view сервера («Бэков: N») без доп. запроса.
    backend_count: int


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
