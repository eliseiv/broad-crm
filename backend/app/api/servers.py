"""Роутер реестра серверов (04-api.md#servers). Все эндпоинты требуют JWT."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, ServerServiceDep
from app.models.server import ProvisionStatus
from app.schemas.server import (
    ServerCreatedResponse,
    ServerCreateRequest,
    ServerListResponse,
    ServerMetricsResponse,
    ServerOrderRequest,
    ServerStatusResponse,
    ServerSummaryResponse,
    ServerUpdateRequest,
)

router = APIRouter(prefix="/servers", tags=["servers"])

StatusFilter = Annotated[ProvisionStatus | None, Query()]


@router.get("", response_model=ServerListResponse)
async def list_servers(
    service: ServerServiceDep,
    _user: CurrentUser,
    status_filter: StatusFilter = None,
) -> ServerListResponse:
    """Список серверов с метриками (position ASC, created_at DESC, id).

    Graceful degradation Prometheus.
    """
    status_value = status_filter.value if status_filter is not None else None
    return await service.list_servers(status=status_value)


@router.post("", response_model=ServerCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_server(
    payload: ServerCreateRequest, service: ServerServiceDep, _user: CurrentUser
) -> ServerCreatedResponse:
    """Создаёт сервер и запускает асинхронный провижининг (202)."""
    return await service.create_server(payload)


@router.patch("/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_servers(
    payload: ServerOrderRequest, service: ServerServiceDep, _user: CurrentUser
) -> Response:
    """Перестановка серверов (полный упорядоченный список id, position=0..N-1)."""
    await service.reorder_servers(payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{server_id}", response_model=ServerSummaryResponse)
async def update_server(
    server_id: uuid.UUID,
    payload: ServerUpdateRequest,
    service: ServerServiceDep,
    _user: CurrentUser,
) -> ServerSummaryResponse:
    """Редактирование сервера — меняет только `name` (200)."""
    return await service.update_server(server_id, payload)


@router.get("/{server_id}/metrics", response_model=ServerMetricsResponse)
async def get_server_metrics(
    server_id: uuid.UUID, service: ServerServiceDep, _user: CurrentUser
) -> ServerMetricsResponse:
    """Текущие метрики одного сервера; Prometheus недоступен → 502."""
    return await service.get_metrics(server_id)


@router.get("/{server_id}/status", response_model=ServerStatusResponse)
async def get_server_status(
    server_id: uuid.UUID, service: ServerServiceDep, _user: CurrentUser
) -> ServerStatusResponse:
    """Лёгкий статус провижининга для прогресс-индикатора."""
    return await service.get_status(server_id)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: uuid.UUID, service: ServerServiceDep, _user: CurrentUser
) -> Response:
    """Удаляет сервер из мониторинга (file_sd + запись)."""
    await service.delete_server(server_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
