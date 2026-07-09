"""Роутер реестра прокси (04-api.md#proxies). RBAC-гейт require(proxies, ...)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import Principal, ProxyServiceDep, require
from app.infra.audit import log_secret_revealed
from app.schemas.proxy import (
    ProxyCreateRequest,
    ProxyListItem,
    ProxyListResponse,
    ProxyOrderRequest,
    ProxyStatusResponse,
    ProxyUpdateRequest,
)
from app.schemas.secret import SecretRevealResponse

router = APIRouter(prefix="/proxies", tags=["proxies"])

ViewDep = Annotated[Principal, Depends(require("proxies", "view"))]
CreateDep = Annotated[Principal, Depends(require("proxies", "create"))]
EditDep = Annotated[Principal, Depends(require("proxies", "edit"))]
DeleteDep = Annotated[Principal, Depends(require("proxies", "delete"))]


@router.get("", response_model=ProxyListResponse)
async def list_proxies(service: ProxyServiceDep, _p: ViewDep) -> ProxyListResponse:
    """Список прокси (position ASC, created_at DESC, id). Пароль не раскрывается."""
    return await service.list_proxies()


@router.post("", response_model=ProxyListItem, status_code=status.HTTP_202_ACCEPTED)
async def create_proxy(
    payload: ProxyCreateRequest, service: ProxyServiceDep, _p: CreateDep
) -> ProxyListItem:
    """Создаёт прокси и запускает немедленную фоновую проверку (202, check_status pending)."""
    return await service.create_proxy(payload)


@router.patch("/order", status_code=status.HTTP_204_NO_CONTENT)
async def reorder_proxies(
    payload: ProxyOrderRequest, service: ProxyServiceDep, _p: EditDep
) -> Response:
    """Перестановка единого списка прокси (position=0..N-1)."""
    await service.reorder_proxies(payload.ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{proxy_id}", response_model=ProxyListItem)
async def update_proxy(
    proxy_id: uuid.UUID,
    payload: ProxyUpdateRequest,
    service: ProxyServiceDep,
    _p: EditDep,
) -> ProxyListItem:
    """Редактирование прокси; re-check при смене связанного с подключением поля (200)."""
    return await service.update_proxy(proxy_id, payload)


@router.get("/{proxy_id}/password", response_model=SecretRevealResponse)
async def reveal_proxy_password(
    proxy_id: uuid.UUID,
    service: ProxyServiceDep,
    principal: EditDep,
    response: Response,
) -> SecretRevealResponse:
    """On-demand reveal пароля прокси (ADR-035, require proxies:edit).

    Нет пароля (`has_password=false`) → 404 secret_not_set. Секрет — в теле ответа
    (не в URL); `Cache-Control: no-store` исключает кэш. Успех → аудит-лог
    `secret_revealed` (без значения).
    """
    value = await service.reveal_password(proxy_id)
    response.headers["Cache-Control"] = "no-store"
    log_secret_revealed(principal, resource_type="proxy", resource_id=str(proxy_id))
    return SecretRevealResponse(value=value)


@router.get("/{proxy_id}/status", response_model=ProxyStatusResponse)
async def get_proxy_status(
    proxy_id: uuid.UUID, service: ProxyServiceDep, _p: ViewDep
) -> ProxyStatusResponse:
    """Лёгкий статус проверки для polling после добавления/редактирования."""
    return await service.get_status(proxy_id)


@router.delete("/{proxy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_proxy(proxy_id: uuid.UUID, service: ProxyServiceDep, _p: DeleteDep) -> Response:
    """Удаляет прокси из реестра (hard delete)."""
    await service.delete_proxy(proxy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
