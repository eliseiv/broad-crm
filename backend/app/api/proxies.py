"""Роутер реестра прокси (04-api.md#proxies). Все эндпоинты требуют JWT."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, ProxyServiceDep
from app.schemas.proxy import (
    ProxyCreateRequest,
    ProxyListItem,
    ProxyListResponse,
    ProxyOrderRequest,
    ProxyStatusResponse,
    ProxyUpdateRequest,
)

router = APIRouter(prefix="/proxies", tags=["proxies"])


@router.get("", response_model=ProxyListResponse)
async def list_proxies(service: ProxyServiceDep, _user: CurrentUser) -> ProxyListResponse:
    """Список прокси (position ASC, created_at DESC, id). Пароль не раскрывается."""
    return await service.list_proxies()


@router.post("", response_model=ProxyListItem, status_code=status.HTTP_202_ACCEPTED)
async def create_proxy(
    payload: ProxyCreateRequest, service: ProxyServiceDep, _user: CurrentUser
) -> ProxyListItem:
    """Создаёт прокси и запускает немедленную фоновую проверку (202, check_status pending)."""
    return await service.create_proxy(payload)


@router.patch("/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_proxies(
    payload: ProxyOrderRequest, service: ProxyServiceDep, _user: CurrentUser
) -> Response:
    """Перестановка единого списка прокси (position=0..N-1)."""
    await service.reorder_proxies(payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{proxy_id}", response_model=ProxyListItem)
async def update_proxy(
    proxy_id: uuid.UUID,
    payload: ProxyUpdateRequest,
    service: ProxyServiceDep,
    _user: CurrentUser,
) -> ProxyListItem:
    """Редактирование прокси; re-check при смене связанного с подключением поля (200)."""
    return await service.update_proxy(proxy_id, payload)


@router.get("/{proxy_id}/status", response_model=ProxyStatusResponse)
async def get_proxy_status(
    proxy_id: uuid.UUID, service: ProxyServiceDep, _user: CurrentUser
) -> ProxyStatusResponse:
    """Лёгкий статус проверки для polling после добавления/редактирования."""
    return await service.get_status(proxy_id)


@router.delete("/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxy(
    proxy_id: uuid.UUID, service: ProxyServiceDep, _user: CurrentUser
) -> Response:
    """Удаляет прокси из реестра (hard delete)."""
    await service.delete_proxy(proxy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
