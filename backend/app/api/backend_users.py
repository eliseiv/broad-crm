"""Роутер страницы «Пользователи бэков» (04-api.md#backend-users). RBAC require(backend-users, ...).

CRM — прокси к CRM Admin API бэков (contract v1): чтение под `backend-users:view`,
admin-операции (токены/подписка) — под `backend-users:edit` + аудит-лог.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import BackendUserServiceDep, Principal, require
from app.infra.audit import log_backend_admin_action
from app.schemas.backend_user import (
    AddBackendUserTokensRequest,
    BackendProductsResponse,
    BackendUserDetailResponse,
    BackendUserGrantResponse,
    BackendUserPaymentsResponse,
    BackendUserRequestsResponse,
    BackendUsersListResponse,
    BackendUserTokensResponse,
    GrantBackendUserSubscriptionRequest,
)

router = APIRouter(prefix="/backend-users", tags=["backend-users"])

ViewDep = Annotated[Principal, Depends(require("backend-users", "view"))]
EditDep = Annotated[Principal, Depends(require("backend-users", "edit"))]

# Лимиты страниц CRM-эндпоинтов (списки историй — контрактный максимум источника).
_LIMIT = Query(default=50, ge=1, le=100)
_OFFSET = Query(default=0, ge=0)


@router.get("", response_model=BackendUsersListResponse)
async def list_backend_users(
    service: BackendUserServiceDep,
    _p: ViewDep,
    backend_id: Annotated[uuid.UUID | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=255)] = None,
    date_from: Annotated[str | None, Query(max_length=32)] = None,
    date_to: Annotated[str | None, Query(max_length=32)] = None,
    is_paid: Annotated[bool | None, Query()] = None,
    limit: int = _LIMIT,
    offset: int = _OFFSET,
) -> BackendUsersListResponse:
    """Объединённый список пользователей бэков (+сводка). `backend_id=None` — все приложения."""
    return await service.list_users(
        backend_id=backend_id,
        search=search,
        date_from=date_from,
        date_to=date_to,
        is_paid=is_paid,
        limit=limit,
        offset=offset,
    )


@router.get("/{backend_id}/products", response_model=BackendProductsResponse)
async def list_backend_products(
    backend_id: uuid.UUID, service: BackendUserServiceDep, _p: ViewDep
) -> BackendProductsResponse:
    """Тарифы бэка для формы «Установить план» (транзит GET {P}/products)."""
    return await service.list_products(backend_id)


@router.get("/{backend_id}/users/{user_id}", response_model=BackendUserDetailResponse)
async def get_backend_user(
    backend_id: uuid.UUID, user_id: str, service: BackendUserServiceDep, _p: ViewDep
) -> BackendUserDetailResponse:
    """Карточка пользователя бэка (баланс/подписка/экономика/генерации)."""
    return await service.get_user(backend_id, user_id)


@router.get("/{backend_id}/users/{user_id}/payments", response_model=BackendUserPaymentsResponse)
async def list_backend_user_payments(
    backend_id: uuid.UUID,
    user_id: str,
    service: BackendUserServiceDep,
    _p: ViewDep,
    limit: int = _LIMIT,
    offset: int = _OFFSET,
) -> BackendUserPaymentsResponse:
    """История оплат пользователя (транзит, сортировка occurred_at DESC)."""
    return await service.list_payments(backend_id, user_id, limit=limit, offset=offset)


@router.get("/{backend_id}/users/{user_id}/requests", response_model=BackendUserRequestsResponse)
async def list_backend_user_requests(
    backend_id: uuid.UUID,
    user_id: str,
    service: BackendUserServiceDep,
    _p: ViewDep,
    limit: int = _LIMIT,
    offset: int = _OFFSET,
) -> BackendUserRequestsResponse:
    """История запросов пользователя (транзит, сортировка sent_at DESC)."""
    return await service.list_requests(backend_id, user_id, limit=limit, offset=offset)


@router.post("/{backend_id}/users/{user_id}/tokens", response_model=BackendUserTokensResponse)
async def add_backend_user_tokens(
    backend_id: uuid.UUID,
    user_id: str,
    payload: AddBackendUserTokensRequest,
    service: BackendUserServiceDep,
    principal: EditDep,
) -> BackendUserTokensResponse:
    """Начислить/списать токены (НЕ идемпотентно, контракт §3.1) + аудит-лог."""
    result = await service.add_tokens(backend_id, user_id, payload)
    log_backend_admin_action(
        principal,
        action="tokens_added",
        backend_id=str(backend_id),
        target_user_id=user_id,
        detail=f"amount={payload.amount}",
    )
    return result


@router.post("/{backend_id}/users/{user_id}/subscription", response_model=BackendUserGrantResponse)
async def grant_backend_user_subscription(
    backend_id: uuid.UUID,
    user_id: str,
    payload: GrantBackendUserSubscriptionRequest,
    service: BackendUserServiceDep,
    principal: EditDep,
) -> BackendUserGrantResponse:
    """Установить/продлить план (идемпотентно по grant_id, контракт §3.2) + аудит-лог."""
    result = await service.grant_subscription(backend_id, user_id, payload)
    log_backend_admin_action(
        principal,
        action="subscription_granted",
        backend_id=str(backend_id),
        target_user_id=user_id,
        detail=f"product_id={payload.product_id} days={payload.expires_in_days}",
    )
    return result
