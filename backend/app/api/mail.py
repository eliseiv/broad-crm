"""Роутер модуля «Почты» (04-api.md#mail). Все эндпоинты требуют JWT.

Read-through-прокси к внешнему сервису `postapp.store` без хранения (ADR-012,
modules/mail). `limit`/гейт `mail_enabled` валидируются в сервисе (контроль
прецеденции: mail_not_configured/mail_unavailable над диапазоном limit).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, MailServiceDep
from app.schemas.mail import (
    MailListResponse,
    MailMailboxesResponse,
    MailOrder,
    MailReplyRequest,
    MailReplyResponse,
    MailTeamsResponse,
)

router = APIRouter(prefix="/mail", tags=["mail"])

Order = Annotated[MailOrder, Query()]
SinceId = Annotated[int | None, Query()]
BeforeId = Annotated[int | None, Query(ge=1)]
Limit = Annotated[int, Query()]
MailAccountId = Annotated[int | None, Query(ge=1)]
GroupId = Annotated[int | None, Query(ge=1)]


@router.get("/messages", response_model=MailListResponse)
async def list_messages(
    service: MailServiceDep,
    _user: CurrentUser,
    order: Order = "desc",
    since_id: SinceId = None,
    before_id: BeforeId = None,
    limit: Limit = 50,
    mail_account_id: MailAccountId = None,
    group_id: GroupId = None,
) -> MailListResponse:
    """Лента писем (04-api.md#mail, ADR-013/ADR-017).

    `order` (`asc`/`desc`, default `desc` — backward newest-first). `since_id` — только
    при `asc`; `before_id` (`ge=1`) — только при `desc`; взаимоисключение → 400.
    `limit` 1..200 (default 50). Серверные фильтры `mail_account_id`/`group_id`
    (`ge=1`, опц.) — взаимоисключающи (оба → 400 `field=filter`), пробрасываются во
    внешний API; несуществующий/чужой `id` → пустая страница.
    """
    return await service.list_messages(
        order=order,
        since_id=since_id,
        before_id=before_id,
        limit=limit,
        mail_account_id=mail_account_id,
        group_id=group_id,
    )


@router.get("/teams", response_model=MailTeamsResponse)
async def list_teams(
    service: MailServiceDep,
    _user: CurrentUser,
) -> MailTeamsResponse:
    """Список команд (прокси external /teams, 04-api.md#mail, ADR-017). Без параметров."""
    return await service.list_teams()


@router.get("/mailboxes", response_model=MailMailboxesResponse)
async def list_mailboxes(
    service: MailServiceDep,
    _user: CurrentUser,
) -> MailMailboxesResponse:
    """Список ящиков (прокси external /mailboxes, 04-api.md#mail, ADR-017). Без параметров."""
    return await service.list_mailboxes()


@router.post("/messages/{message_id}/reply", response_model=MailReplyResponse)
async def reply_message(
    message_id: int,
    payload: MailReplyRequest,
    service: MailServiceDep,
    _user: CurrentUser,
) -> MailReplyResponse:
    """Ответ на письмо (прокси к внешнему reply-эндпоинту)."""
    return await service.reply(message_id=message_id, payload=payload)
