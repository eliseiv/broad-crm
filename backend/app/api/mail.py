"""Роутер модуля «Почты» (04-api.md#mail, ADR-012/038). Матрица прав `mail:*`.

Headless read+write прокси к внешнему сервису `postapp.store` без хранения. Гейты:
`view` (лента/справочники/теги-чтение + reply), `create` (создание/тест ящика),
`edit` (правка ящика), `delete` (удаление ящика), `sync` (форс-синк), `tags`
(управление каталогом тегов). Мутации ящика дополнительно ограничены `MailScope`
(вне scope → 403). Эндпоинты записи ящиков несут `Cache-Control: no-store` (в теле
запроса транзитом идут IMAP/SMTP-креды, 05-security.md). `limit`/гейт `mail_enabled`
валидируются в сервисе.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import MailScopeDep, MailServiceDep, Principal, require
from app.schemas.mail import (
    MailListResponse,
    MailMailbox,
    MailMailboxCreateRequest,
    MailMailboxesResponse,
    MailMailboxSyncResponse,
    MailMailboxTestRequest,
    MailMailboxTestResponse,
    MailMailboxUpdateRequest,
    MailOrder,
    MailReplyRequest,
    MailReplyResponse,
    MailTagApplyResponse,
    MailTagCreateRequest,
    MailTagFull,
    MailTagRule,
    MailTagRuleCreateRequest,
    MailTagsResponse,
    MailTagUpdateRequest,
    MailTeamsResponse,
)

router = APIRouter(prefix="/mail", tags=["mail"])

ViewDep = Annotated[Principal, Depends(require("mail", "view"))]
CreateDep = Annotated[Principal, Depends(require("mail", "create"))]
EditDep = Annotated[Principal, Depends(require("mail", "edit"))]
DeleteDep = Annotated[Principal, Depends(require("mail", "delete"))]
SyncDep = Annotated[Principal, Depends(require("mail", "sync"))]
TagsDep = Annotated[Principal, Depends(require("mail", "tags"))]

Order = Annotated[MailOrder, Query()]
SinceId = Annotated[int | None, Query()]
BeforeId = Annotated[int | None, Query(ge=1)]
Limit = Annotated[int, Query()]
MailAccountId = Annotated[int | None, Query(ge=1)]
GroupId = Annotated[int | None, Query(ge=1)]
IsActive = Annotated[bool | None, Query()]


# --- Чтение -----------------------------------------------------------------


@router.get("/messages", response_model=MailListResponse)
async def list_messages(
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: ViewDep,
    order: Order = "desc",
    since_id: SinceId = None,
    before_id: BeforeId = None,
    limit: Limit = 50,
    mail_account_id: MailAccountId = None,
    group_id: GroupId = None,
) -> MailListResponse:
    """Лента писем (04-api.md#mail, ADR-013/017/038).

    Фильтры `mail_account_id`/`group_id` (`ge=1`, опц.) AND-комбинируемы; для не-админа
    пересекаются со `MailScope.group_ids` (вне scope → пустая страница, анти-энумерация).
    """
    return await service.list_messages(
        scope=scope,
        order=order,
        since_id=since_id,
        before_id=before_id,
        limit=limit,
        mail_account_id=mail_account_id,
        group_id=group_id,
    )


@router.get("/teams", response_model=MailTeamsResponse)
async def list_teams(service: MailServiceDep, _p: ViewDep) -> MailTeamsResponse:
    """Список команд (прокси external /teams, 04-api.md#mail). Без параметров."""
    return await service.list_teams()


@router.get("/mailboxes", response_model=MailMailboxesResponse)
async def list_mailboxes(
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: ViewDep,
    is_active: IsActive = None,
    group_id: GroupId = None,
) -> MailMailboxesResponse:
    """Список ящиков (прокси external /mailboxes, 04-api.md#mail, ADR-038).

    Фильтруется `MailScope` (не-admin — только ящики групп своих команд). `is_active`/
    `group_id` пробрасываются во внешний API.
    """
    return await service.list_mailboxes(scope=scope, is_active=is_active, group_id=group_id)


@router.get("/tags", response_model=MailTagsResponse)
async def list_tags(service: MailServiceDep, _p: ViewDep) -> MailTagsResponse:
    """Список глобальных тегов с правилами (прокси external /tags, 04-api.md#mail)."""
    return await service.list_tags()


@router.post("/messages/{message_id}/reply", response_model=MailReplyResponse)
async def reply_message(
    message_id: int,
    payload: MailReplyRequest,
    service: MailServiceDep,
    _p: ViewDep,
) -> MailReplyResponse:
    """Ответ на письмо (прокси к внешнему reply-эндпоинту). Гейт mail:view (ADR-012)."""
    return await service.reply(message_id=message_id, payload=payload)


# --- Запись: почтовые ящики -------------------------------------------------


@router.post("/mailboxes/test", response_model=MailMailboxTestResponse)
async def test_mailbox(
    payload: MailMailboxTestRequest,
    service: MailServiceDep,
    _p: CreateDep,
    response: Response,
) -> MailMailboxTestResponse:
    """Проверка IMAP/SMTP-соединения без сохранения (04-api.md#mail). Гейт mail:create."""
    response.headers["Cache-Control"] = "no-store"
    return await service.test_mailbox(payload)


@router.post("/mailboxes", response_model=MailMailbox, status_code=status.HTTP_201_CREATED)
async def create_mailbox(
    payload: MailMailboxCreateRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: CreateDep,
    response: Response,
) -> MailMailbox:
    """Создание ящика (04-api.md#mail). Гейт mail:create; для не-admin group_id ∈ scope."""
    response.headers["Cache-Control"] = "no-store"
    return await service.create_mailbox(scope=scope, payload=payload)


@router.patch("/mailboxes/{mailbox_id}", response_model=MailMailbox)
async def update_mailbox(
    mailbox_id: int,
    payload: MailMailboxUpdateRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: EditDep,
    response: Response,
) -> MailMailbox:
    """Правка ящика (presence-семантика, 04-api.md#mail). Гейт mail:edit; ящик ∈ scope."""
    response.headers["Cache-Control"] = "no-store"
    return await service.update_mailbox(scope=scope, mailbox_id=mailbox_id, payload=payload)


@router.delete("/mailboxes/{mailbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mailbox(
    mailbox_id: int,
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: DeleteDep,
) -> Response:
    """Удаление ящика (04-api.md#mail). Гейт mail:delete; ящик ∈ scope."""
    await service.delete_mailbox(scope=scope, mailbox_id=mailbox_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/mailboxes/{mailbox_id}/sync",
    response_model=MailMailboxSyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def sync_mailbox(
    mailbox_id: int,
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: SyncDep,
) -> MailMailboxSyncResponse:
    """Форс-синк ящика (04-api.md#mail). Гейт mail:sync; ящик ∈ scope."""
    return await service.sync_mailbox(scope=scope, mailbox_id=mailbox_id)


# --- Запись: теги (глобальный каталог, гейт mail:tags) ----------------------


@router.post("/tags", response_model=MailTagFull, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: MailTagCreateRequest,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagFull:
    """Создание тега (04-api.md#mail). Гейт mail:tags."""
    return await service.create_tag(payload)


@router.patch("/tags/{tag_id}", response_model=MailTagFull)
async def update_tag(
    tag_id: int,
    payload: MailTagUpdateRequest,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagFull:
    """Правка тега (04-api.md#mail). Гейт mail:tags."""
    return await service.update_tag(tag_id, payload)


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: int,
    service: MailServiceDep,
    _p: TagsDep,
) -> Response:
    """Удаление тега (04-api.md#mail). Гейт mail:tags; встроенный тег → 409."""
    await service.delete_tag(tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tags/{tag_id}/rules", response_model=MailTagRule, status_code=status.HTTP_201_CREATED
)
async def create_tag_rule(
    tag_id: int,
    payload: MailTagRuleCreateRequest,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagRule:
    """Добавление правила тегу (04-api.md#mail). Гейт mail:tags."""
    return await service.create_tag_rule(tag_id, payload)


@router.delete("/tags/{tag_id}/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag_rule(
    tag_id: int,
    rule_id: int,
    service: MailServiceDep,
    _p: TagsDep,
) -> Response:
    """Удаление правила (04-api.md#mail). Гейт mail:tags."""
    await service.delete_tag_rule(tag_id, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tags/{tag_id}/apply-to-existing", response_model=MailTagApplyResponse)
async def apply_tag_to_existing(
    tag_id: int,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagApplyResponse:
    """Применить правила тега к существующим письмам (04-api.md#mail). Гейт mail:tags."""
    return await service.apply_tag_to_existing(tag_id)
