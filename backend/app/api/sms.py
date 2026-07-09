"""Приватный роутер модуля «СМС» (04-api.md#sms, ADR-030). Гейт матрицы `sms:*`.

Лента сообщений + реестр номеров (правка/перенос/удаление/синк) + Telegram-привязка
оператора. Видимость — по scope (`SmsScopeDep`). `POST /api/sms/telegram/link` —
только аутентификация (вне матрицы `sms`): любой валидный JWT привязывает свой
Telegram (ADR-030 §7). Публичные webhook'и/auth — в `sms_webhooks.py`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import (
    Principal,
    PrincipalDep,
    SmsMessageServiceDep,
    SmsNumberServiceDep,
    SmsScopeDep,
    SmsSyncServiceDep,
    SmsTelegramLinkServiceDep,
    require,
)
from app.schemas.sms import (
    SmsMessagesResponse,
    SmsNumberItem,
    SmsNumbersResponse,
    SmsNumberTransferRequest,
    SmsNumberUpdateRequest,
    SmsSyncResult,
    TelegramLinkRequest,
    TelegramLinkResponse,
)

router = APIRouter(prefix="/sms", tags=["sms"])

ViewDep = Annotated[Principal, Depends(require("sms", "view"))]
EditDep = Annotated[Principal, Depends(require("sms", "edit"))]
TransferDep = Annotated[Principal, Depends(require("sms", "transfer"))]
SyncDep = Annotated[Principal, Depends(require("sms", "sync"))]
DeleteDep = Annotated[Principal, Depends(require("sms", "delete"))]

NumberIdFilter = Annotated[int | None, Query()]
TeamIdFilter = Annotated[uuid.UUID | None, Query()]
Cursor = Annotated[str | None, Query()]
Limit = Annotated[int, Query()]


@router.get("/messages", response_model=SmsMessagesResponse)
async def list_messages(
    service: SmsMessageServiceDep,
    scope: SmsScopeDep,
    _p: ViewDep,
    number_id: NumberIdFilter = None,
    team_id: TeamIdFilter = None,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> SmsMessagesResponse:
    """Лента входящих SMS (newest-first, keyset-курсор). Фильтры комбинируемы (AND).

    `number_id`/`team_id` вне scope → пустая страница (анти-энумерация). Битый
    `cursor` → 400 invalid_cursor; `limit` вне [1,100] → 400 invalid_limit.
    """
    return await service.list_messages(
        scope=scope,
        number_id=number_id,
        team_id=team_id,
        cursor=cursor,
        limit=limit,
    )


@router.get("/numbers", response_model=SmsNumbersResponse)
async def list_numbers(
    service: SmsNumberServiceDep,
    scope: SmsScopeDep,
    _p: ViewDep,
) -> SmsNumbersResponse:
    """Список номеров (супер-админ — все, включая unassigned; не-админ — своих команд)."""
    return await service.list_numbers(scope)


@router.post("/numbers/sync", response_model=SmsSyncResult)
async def sync_numbers(service: SmsSyncServiceDep, _p: SyncDep) -> SmsSyncResult:
    """Синхронизация входящих номеров Twilio в пул как unassigned (503/502 при сбоях)."""
    return await service.sync()


@router.patch("/numbers/{number_id}", response_model=SmsNumberItem)
async def update_number(
    number_id: int,
    payload: SmsNumberUpdateRequest,
    service: SmsNumberServiceDep,
    scope: SmsScopeDep,
    _p: EditDep,
) -> SmsNumberItem:
    """Правка `login`/`app_name`/`note` (presence-семантика). Вне scope → 403; нет → 404."""
    return await service.update_number(scope, number_id, payload)


@router.post("/numbers/{number_id}/transfer", response_model=SmsNumberItem)
async def transfer_number(
    number_id: int,
    payload: SmsNumberTransferRequest,
    service: SmsNumberServiceDep,
    scope: SmsScopeDep,
    _p: TransferDep,
) -> SmsNumberItem:
    """Назначить/снять команду у номера. Вне scope → 403; чужой team_id → 404."""
    return await service.transfer_number(scope, number_id, payload)


@router.delete("/numbers/{number_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_number(
    number_id: int,
    service: SmsNumberServiceDep,
    scope: SmsScopeDep,
    _p: DeleteDep,
) -> Response:
    """Удалить номер (история SMS сохраняется). Вне scope → 403; нет → 404."""
    await service.delete_number(scope, number_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/telegram/link", response_model=TelegramLinkResponse)
async def telegram_link(
    payload: TelegramLinkRequest,
    service: SmsTelegramLinkServiceDep,
    principal: PrincipalDep,
) -> TelegramLinkResponse:
    """Привязка своего Telegram к своему CRM-юзеру (только JWT, вне матрицы `sms`).

    Супер-админ без `uid` привязать линк не может → 403 forbidden (ADR-030 §7).
    """
    return await service.link(user_id=principal.user_id, init_data=payload.init_data)
