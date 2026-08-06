"""Роутер страницы «Продукты и тарифы» (04-api.md#backend-economics, ADR-072).

Отдельный роутер, а не расширение `backend_users.py` (ADR-072 §2): другой RBAC-гейт
и ресурсы, не вложенные в пользователя. Ключ `backend-economics` — **не алиас**
`backend-users:edit`: роль с полным `backend-users:["view","edit"]` и без
`backend-economics` получает 403 на ВСЕХ путях ниже. Чтение — `view`, правка — `edit`
+ аудит-событие, которое пишется **только после успеха бэка** (ADR-072 §10).

`backend_user_not_found` с этого роутера **недостижим** — путей с пользователем в нём
нет (ADR-072 §4г); появление этого кода здесь = дефект, а не состояние.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import BackendEconomicsServiceDep, Principal, require
from app.infra.audit import log_backend_admin_action
from app.schemas.backend_economics import (
    BackendEconomicsBackendsResponse,
    BackendEconomicsPricingResponse,
    BackendEconomicsProductsResponse,
    BackendProductUpdateResponse,
    BackendTariffUpdateResponse,
    UpdateBackendProductRequest,
    UpdateBackendTariffRequest,
)

router = APIRouter(prefix="/backend-economics", tags=["backend-economics"])

ViewDep = Annotated[Principal, Depends(require("backend-economics", "view"))]
EditDep = Annotated[Principal, Depends(require("backend-economics", "edit"))]


def _amount(value: float | None) -> str:
    """Число для аудит-записи: целое — без дробной части, отсутствующее — не `None`.

    Нормативный образец дельты — `1000->1500` (ADR-072 §10, 04-api.md#backend-economics),
    поэтому `float`, равный целому (тарифы всегда `number`), печатается целым: `1000.0`
    в записи о деньгах — шум. Отсутствующее значение печатается `n/a`, а не `None`:
    запись `tokens=1000->None` утверждала бы установку `None`, которой не было.
    """
    if value is None:
        return "n/a"
    return str(int(value)) if float(value).is_integer() else str(value)


def _delta(previous: float | None, current: float | None) -> str:
    """Дельта правки для аудита (`1000->1500`, ADR-072 §8/§10).

    Правка глобальна и без отката, поэтому дельта обязательна, а не желательна.
    """
    return f"{_amount(previous)}->{_amount(current)}"


def _product_detail(
    product_id: str, payload: UpdateBackendProductRequest, result: BackendProductUpdateResponse
) -> str:
    """Деталь аудита правки продукта — называет ИМЕННО ИЗМЕНЁННОЕ поле (ADR-072 §10).

    `previous_tokens` относится ТОЛЬКО к `tokens`, поэтому безусловная дельта токенов
    при правке одних аватар-токенов дала бы `tokens=1000->1000`: запись сообщает о
    правке, которой не было, и умалчивает о той, которая была. Норма:

    - `tokens=<prev>-><new>` — только если `tokens` был в теле запроса;
    - `avatar_tokens=<new>` — только если в теле был он; **прежнее значение не пишется**:
      авторитетного `previous_avatar_tokens` контракт не даёт, а подставлять сюда
      `previous_tokens` запрещено (04-api.md#backend-economics) — это ложное утверждение
      о другой денежной величине. Долг зафиксирован (TD-080), выдумывать прежнее
      значение на стороне CRM нельзя;
    - правка обеих величин одним `PATCH` → обе части в одной записи.

    Аудит — единственный постоянный след глобального необратимого изменения, поэтому
    «примерно верная» запись здесь хуже отсутствующей.
    """
    parts = [f"product_id={product_id}"]
    if payload.tokens is not None:
        parts.append(f"tokens={_delta(result.previous_tokens, result.tokens)}")
    if payload.avatar_tokens is not None:
        # Новое значение — из ответа бэка (авторитетный итог); если бэк поле не вернул,
        # берём отправленное оператором. Прежнее не подставляется ни из какого источника.
        applied = (
            result.avatar_tokens if result.avatar_tokens is not None else payload.avatar_tokens
        )
        parts.append(f"avatar_tokens={_amount(applied)}")
    return " ".join(parts)


@router.get("/backends", response_model=BackendEconomicsBackendsResponse)
async def list_economics_backends(
    service: BackendEconomicsServiceDep, _p: ViewDep
) -> BackendEconomicsBackendsResponse:
    """Бэки с Admin API Key для селектора приложения (гейт — `backend-economics:view`)."""
    return await service.list_backends()


@router.get("/{backend_id}/products", response_model=BackendEconomicsProductsResponse)
async def list_backend_economics_products(
    backend_id: uuid.UUID, service: BackendEconomicsServiceDep, _p: ViewDep
) -> BackendEconomicsProductsResponse:
    """Полный каталог продуктов бэка (`scope=all`) + конверт `capabilities`."""
    return await service.list_products(backend_id)


@router.patch("/{backend_id}/products/{product_id}", response_model=BackendProductUpdateResponse)
async def update_backend_economics_product(
    backend_id: uuid.UUID,
    product_id: str,
    payload: UpdateBackendProductRequest,
    service: BackendEconomicsServiceDep,
    principal: EditDep,
) -> BackendProductUpdateResponse:
    """Правка токенов продукта (идемпотентно) + аудит-лог после успеха бэка."""
    result = await service.update_product(
        backend_id, product_id, payload, actor_id=principal.user_id
    )
    log_backend_admin_action(
        principal,
        action="product_tokens_updated",
        backend_id=str(backend_id),
        detail=_product_detail(product_id, payload, result),
    )
    return result


@router.get("/{backend_id}/pricing", response_model=BackendEconomicsPricingResponse)
async def list_backend_economics_pricing(
    backend_id: uuid.UUID, service: BackendEconomicsServiceDep, _p: ViewDep
) -> BackendEconomicsPricingResponse:
    """Тарифы списания за генерацию + конверт `capabilities`."""
    return await service.list_pricing(backend_id)


@router.patch("/{backend_id}/pricing/{tariff_id}", response_model=BackendTariffUpdateResponse)
async def update_backend_economics_pricing(
    backend_id: uuid.UUID,
    tariff_id: str,
    payload: UpdateBackendTariffRequest,
    service: BackendEconomicsServiceDep,
    principal: EditDep,
) -> BackendTariffUpdateResponse:
    """Правка тарифа списания (идемпотентно) + аудит-лог после успеха бэка."""
    result = await service.update_pricing(
        backend_id, tariff_id, payload, actor_id=principal.user_id
    )
    log_backend_admin_action(
        principal,
        action="pricing_updated",
        backend_id=str(backend_id),
        detail=f"tariff_id={tariff_id} tokens={_delta(result.previous_tokens, result.tokens)}",
    )
    return result
