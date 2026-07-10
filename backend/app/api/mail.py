"""Роутер модуля «Почты» (ADR-044). Матрица прав `mail:*`.

CRM — система-запись: лента/ящики/теги читаются из БД CRM; создание/правка/удаление
ящика и reply транзитом делегируются агрегатору (креды не хранятся в CRM). Гейты:
`view` (лента/ящики/теги-чтение + reply), `create` (создание/тест ящика), `edit`
(правка ящика), `delete` (удаление ящика), `sync` (форс-синк), `tags` (управление
глобальным каталогом тегов). Мутации/синк/удаление ящика и reply дополнительно
ограничены `MailScope` по `team_id` (вне scope → 403/404, анти-энумерация). Эндпоинты
записи ящиков несут `Cache-Control: no-store` (в телах транзитом идут IMAP/SMTP-креды,
05-security.md).
"""

from __future__ import annotations

import uuid
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
    MailReplyRequest,
    MailReplyResponse,
    MailTagApplyResponse,
    MailTagCreateRequest,
    MailTagFull,
    MailTagRule,
    MailTagRuleCreateRequest,
    MailTagsResponse,
    MailTagUpdateRequest,
)

router = APIRouter(prefix="/mail", tags=["mail"])

ViewDep = Annotated[Principal, Depends(require("mail", "view"))]
CreateDep = Annotated[Principal, Depends(require("mail", "create"))]
EditDep = Annotated[Principal, Depends(require("mail", "edit"))]
DeleteDep = Annotated[Principal, Depends(require("mail", "delete"))]
SyncDep = Annotated[Principal, Depends(require("mail", "sync"))]
TagsDep = Annotated[Principal, Depends(require("mail", "tags"))]

Before = Annotated[str | None, Query()]
Limit = Annotated[int, Query()]
MailAccountIds = Annotated[list[int] | None, Query(alias="mail_account_id")]
TeamId = Annotated[uuid.UUID | None, Query()]
IsActive = Annotated[bool | None, Query()]


# --- Чтение (из БД CRM) -----------------------------------------------------


@router.get("/messages", response_model=MailListResponse)
async def list_messages(
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: ViewDep,
    before: Before = None,
    limit: Limit = 50,
    mail_account_id: MailAccountIds = None,
    team_id: TeamId = None,
) -> MailListResponse:
    """Лента писем из `mail_messages` (компаундный keyset, ADR-044 §2/§7).

    Порядок `internal_date DESC, id DESC`. Фильтры `mail_account_id` (повторяемый) и
    `team_id` AND-комбинируемы; для не-админа пересекаются со `MailScope.team_ids` (вне
    scope → пустая страница, анти-энумерация). `before` — opaque-курсор пары
    `(internal_date, id)`; `limit` в диапазоне 1..200.
    """
    return await service.list_messages(
        scope=scope,
        before=before,
        limit=limit,
        mail_account_ids=mail_account_id,
        team_id=team_id,
    )


@router.get("/mailboxes", response_model=MailMailboxesResponse)
async def list_mailboxes(
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: ViewDep,
    is_active: IsActive = None,
) -> MailMailboxesResponse:
    """Список ящиков из каталога CRM `mail_accounts` (ADR-044 §4/§7).

    Не-admin — только ящики своих команд (`team_id ∈ scope.team_ids`, анти-энумерация).
    `is_active` — доп. фильтр активности.
    """
    return await service.list_mailboxes(scope=scope, is_active=is_active)


@router.get("/tags", response_model=MailTagsResponse)
async def list_tags(service: MailServiceDep, _p: ViewDep) -> MailTagsResponse:
    """Список глобальных тегов с правилами из БД CRM (ADR-044 §5)."""
    return await service.list_tags()


@router.post("/messages/{message_id}/reply", response_model=MailReplyResponse)
async def reply_message(
    message_id: int,
    payload: MailReplyRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> MailReplyResponse:
    """Ответ на письмо (ADR-044 §8). Гейт mail:view; письмо ∈ scope (иначе 404).

    Письмо берётся из БД CRM, SMTP-отправка делегируется агрегатору, факт отправки
    пишется в `mail_sent_messages`.
    """
    return await service.reply(
        scope=scope, user_id=p.user_id, message_id=message_id, payload=payload
    )


# --- Запись: почтовые ящики (креды транзитом в агрегатор) -------------------


@router.post("/mailboxes/test", response_model=MailMailboxTestResponse)
async def test_mailbox(
    payload: MailMailboxTestRequest,
    service: MailServiceDep,
    _p: CreateDep,
    response: Response,
) -> MailMailboxTestResponse:
    """Проверка IMAP/SMTP-соединения без сохранения (ADR-044 §4). Гейт mail:create."""
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
    """Создание ящика (ADR-044 §4). Гейт mail:create; для не-admin team_id ∈ scope."""
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
    """Правка ящика (presence-семантика, ADR-044 §4). Гейт mail:edit; ящик ∈ scope.

    Смена `team_id` (перенос между командами) — только admin-уровень.
    """
    response.headers["Cache-Control"] = "no-store"
    return await service.update_mailbox(scope=scope, mailbox_id=mailbox_id, payload=payload)


@router.delete("/mailboxes/{mailbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mailbox(
    mailbox_id: int,
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: DeleteDep,
) -> Response:
    """Удаление ящика (ADR-044 §4). Гейт mail:delete; ящик ∈ scope."""
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
    """Форс-синк ящика (ADR-044 §4). Гейт mail:sync; ящик ∈ scope."""
    return await service.sync_mailbox(scope=scope, mailbox_id=mailbox_id)


# --- Запись: теги (глобальный каталог, гейт mail:tags) ----------------------


@router.post("/tags", response_model=MailTagFull, status_code=status.HTTP_201_CREATED)
async def create_tag(
    payload: MailTagCreateRequest,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagFull:
    """Создание тега (ADR-044 §5). Гейт mail:tags."""
    return await service.create_tag(payload)


@router.patch("/tags/{tag_id}", response_model=MailTagFull)
async def update_tag(
    tag_id: uuid.UUID,
    payload: MailTagUpdateRequest,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagFull:
    """Правка тега (ADR-044 §5). Гейт mail:tags."""
    return await service.update_tag(tag_id, payload)


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: uuid.UUID,
    service: MailServiceDep,
    _p: TagsDep,
) -> Response:
    """Удаление тега (ADR-044 §5). Гейт mail:tags; встроенный тег → 409."""
    await service.delete_tag(tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/tags/{tag_id}/rules", response_model=MailTagRule, status_code=status.HTTP_201_CREATED
)
async def create_tag_rule(
    tag_id: uuid.UUID,
    payload: MailTagRuleCreateRequest,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagRule:
    """Добавление правила тегу (ADR-044 §5). Гейт mail:tags."""
    return await service.create_tag_rule(tag_id, payload)


@router.delete("/tags/{tag_id}/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag_rule(
    tag_id: uuid.UUID,
    rule_id: uuid.UUID,
    service: MailServiceDep,
    _p: TagsDep,
) -> Response:
    """Удаление правила (ADR-044 §5). Гейт mail:tags."""
    await service.delete_tag_rule(tag_id, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tags/{tag_id}/apply-to-existing", response_model=MailTagApplyResponse)
async def apply_tag_to_existing(
    tag_id: uuid.UUID,
    service: MailServiceDep,
    _p: TagsDep,
) -> MailTagApplyResponse:
    """Применить правила тега к существующим письмам (ADR-044 §5). Гейт mail:tags."""
    return await service.apply_tag_to_existing(tag_id)
