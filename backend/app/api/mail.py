"""Роутер модуля «Почты» (04-api.md#mail). Все эндпоинты требуют JWT.

Read-through-прокси к внешнему сервису `postapp.store` без хранения (ADR-012,
modules/mail). `limit`/гейт `mail_enabled` валидируются в сервисе (контроль
прецеденции: mail_not_configured/mail_unavailable над диапазоном limit).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, MailServiceDep
from app.schemas.mail import MailListResponse, MailOrder, MailReplyRequest, MailReplyResponse

router = APIRouter(prefix="/mail", tags=["mail"])

Order = Annotated[MailOrder, Query()]
SinceId = Annotated[int | None, Query()]
BeforeId = Annotated[int | None, Query(ge=1)]
Limit = Annotated[int, Query()]


@router.get("/messages", response_model=MailListResponse)
async def list_messages(
    service: MailServiceDep,
    _user: CurrentUser,
    order: Order = "desc",
    since_id: SinceId = None,
    before_id: BeforeId = None,
    limit: Limit = 50,
) -> MailListResponse:
    """Лента писем (04-api.md#mail, ADR-013).

    `order` (`asc`/`desc`, default `desc` — backward newest-first). `since_id` — только
    при `asc`; `before_id` (`ge=1`) — только при `desc`; взаимоисключение → 400.
    `limit` 1..200 (default 50).
    """
    return await service.list_messages(
        order=order, since_id=since_id, before_id=before_id, limit=limit
    )


@router.post("/messages/{message_id}/reply", response_model=MailReplyResponse)
async def reply_message(
    message_id: int,
    payload: MailReplyRequest,
    service: MailServiceDep,
    _user: CurrentUser,
) -> MailReplyResponse:
    """Ответ на письмо (прокси к внешнему reply-эндпоинту)."""
    return await service.reply(message_id=message_id, payload=payload)
