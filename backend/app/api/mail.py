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
    MailComposeRequest,
    MailMessageBatchRequest,
    MailOauthAuthorizeRequest,
    MailOauthAuthorizeResponse,
    MailReplyRequest,
    MailReplyResponse,
    MailSentListResponse,
    MailSentMessage,
    MailTagApplyResponse,
    MailTagCreateRequest,
    MailTagFull,
    MailTagRule,
    MailTagRuleCreateRequest,
    MailTagsResponse,
    MailTagUpdateRequest,
    MailUnreadCountResponse,
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
Unread = Annotated[bool | None, Query()]
# Фильтр «Без команды» ленты (ADR-055 §5.3): true → только письма ящиков с
# `team_id IS NULL`. Взаимоисключающ с `team_id` (оба → 400 validation_error).
NoTeam = Annotated[bool | None, Query()]
Folder = Annotated[str | None, Query()]
HasTags = Annotated[bool | None, Query()]
TagId = Annotated[uuid.UUID | None, Query()]


# --- Чтение (из БД CRM) -----------------------------------------------------


@router.get("/messages", response_model=MailListResponse)
async def list_messages(
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
    before: Before = None,
    limit: Limit = 50,
    mail_account_id: MailAccountIds = None,
    team_id: TeamId = None,
    no_team: NoTeam = None,
    unread: Unread = None,
    folder: Folder = None,
    has_tags: HasTags = None,
    tag_id: TagId = None,
) -> MailListResponse:
    """Лента писем из `mail_messages` (компаундный keyset, ADR-044 §2/§7, ADR-071)."""
    folder_val = folder if folder in ("inbox", "archived", "deleted") else "inbox"
    return await service.list_messages(
        scope=scope,
        user_id=p.user_id,
        before=before,
        limit=limit,
        mail_account_ids=mail_account_id,
        team_id=team_id,
        no_team=no_team,
        unread=unread,
        folder=folder_val,
        has_tags=has_tags,
        tag_id=tag_id,
    )


@router.get("/unread-count", response_model=MailUnreadCountResponse)
async def unread_count(
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
    mail_account_id: MailAccountIds = None,
    team_id: TeamId = None,
    no_team: NoTeam = None,
) -> MailUnreadCountResponse:
    """Счётчик непрочитанных во входящих (ADR-071)."""
    return await service.unread_count(
        scope=scope,
        user_id=p.user_id,
        mail_account_ids=mail_account_id,
        team_id=team_id,
        no_team=no_team,
    )


@router.get("/sent", response_model=MailSentListResponse)
async def list_sent(
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: ViewDep,
    before: Before = None,
    limit: Limit = 50,
    mail_account_id: MailAccountIds = None,
    team_id: TeamId = None,
    no_team: NoTeam = None,
) -> MailSentListResponse:
    """Лента отправленных reply из CRM (ADR-071)."""
    return await service.list_sent(
        scope=scope,
        before=before,
        limit=limit,
        mail_account_ids=mail_account_id,
        team_id=team_id,
        no_team=no_team,
    )


@router.get("/sent/{sent_id}", response_model=MailSentMessage)
async def get_sent(
    sent_id: uuid.UUID,
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: ViewDep,
) -> MailSentMessage:
    """Деталь отправленного письма (ADR-071)."""
    return await service.get_sent(scope=scope, sent_id=sent_id)


@router.get("/mailboxes", response_model=MailMailboxesResponse)
async def list_mailboxes(
    service: MailServiceDep,
    scope: MailScopeDep,
    _p: ViewDep,
    is_active: IsActive = None,
) -> MailMailboxesResponse:
    """Список ящиков из каталога CRM `mail_accounts` (ADR-044 §4/§7).

    Единый предикат scope (ADR-055 §3): не-admin — ящики своих команд (базовые ∪
    доп-команды канала) **плюс бесхозные при `mail_includes_unassigned=true`**; вне scope —
    пусто (анти-энумерация). Admin-уровень — все. `is_active` — доп. фильтр активности.
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


# --- Личная прочитанность писем (ADR-050 §2) --------------------------------


@router.post("/messages/{message_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_message_read(
    message_id: int,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> Response:
    """Пометить письмо прочитанным ТЕКУЩИМ пользователем (ADR-050 §2.2). Гейт mail:view.

    Прочитанность личная: отметка не влияет на других членов команды. Идемпотентен
    (повтор → тоже 204, `read_at` не обновляется). Письмо вне scope/несуществующее → 404
    (анти-энумерация). Супер-админ из `.env` (нет строки в `users`) → 403 (§2.5).
    Вызывается UI при ОТКРЫТИИ письма — и в вебе `/mail`, и в Mini App `/tg/mail` (та же
    ручка под тем же CRM-JWT).
    """
    await service.mark_read(scope=scope, user_id=p.user_id, message_id=message_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/messages/{message_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def unmark_message_read(
    message_id: int,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> Response:
    """Вернуть письмо в «непрочитано» для текущего пользователя (ADR-050 §2.7).

    Гейт mail:view. Идемпотентен (отметки не было → тоже 204). Те же 403/404, что у POST.
    """
    await service.unmark_read(scope=scope, user_id=p.user_id, message_id=message_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages/batch/read", status_code=status.HTTP_204_NO_CONTENT)
async def batch_mark_read(
    payload: MailMessageBatchRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> Response:
    """Пометить несколько писем прочитанными (ADR-071)."""
    await service.batch_read(
        scope=scope, user_id=p.user_id, message_ids=payload.message_ids
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages/batch/archive", status_code=status.HTTP_204_NO_CONTENT)
async def batch_archive(
    payload: MailMessageBatchRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> Response:
    """Архивировать письма (ADR-071)."""
    await service.batch_archive(
        scope=scope, user_id=p.user_id, message_ids=payload.message_ids
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages/batch/delete", status_code=status.HTTP_204_NO_CONTENT)
async def batch_delete(
    payload: MailMessageBatchRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> Response:
    """Удалить письма в корзину (ADR-071)."""
    await service.batch_delete(
        scope=scope, user_id=p.user_id, message_ids=payload.message_ids
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages/batch/unarchive", status_code=status.HTTP_204_NO_CONTENT)
async def batch_unarchive(
    payload: MailMessageBatchRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> Response:
    """Вернуть из архива (ADR-071)."""
    await service.batch_unarchive(
        scope=scope, user_id=p.user_id, message_ids=payload.message_ids
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/messages/batch/restore", status_code=status.HTTP_204_NO_CONTENT)
async def batch_restore(
    payload: MailMessageBatchRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> Response:
    """Восстановить из корзины (ADR-071)."""
    await service.batch_restore(
        scope=scope, user_id=p.user_id, message_ids=payload.message_ids
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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


@router.post("/mailboxes/oauth/authorize", response_model=MailOauthAuthorizeResponse)
async def authorize_oauth(
    payload: MailOauthAuthorizeRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: CreateDep,
    response: Response,
) -> MailOauthAuthorizeResponse:
    """Инициировать headless Outlook-OAuth из CRM (ADR-045 §3). Гейт mail:create.

    Авторизация команды — как создание ящика (`team_id ∈ scope`; `team_id=null` — только
    admin). CRM минтит HMAC-подписанный `crm_state` и запрашивает у агрегатора authorize
    URL. Outlook-OAuth недоступен (`MAIL_API_KEY` пуст ИЛИ агрегатор вернул 404) → единый
    503 mail_not_configured (frontend по нему скрывает кнопку Outlook, §5).
    """
    response.headers["Cache-Control"] = "no-store"
    return await service.authorize_oauth(scope=scope, initiator_user_id=p.user_id, payload=payload)


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


@router.post(
    "/mailboxes/{mailbox_id}/compose",
    response_model=MailReplyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def compose_mail(
    mailbox_id: int,
    payload: MailComposeRequest,
    service: MailServiceDep,
    scope: MailScopeDep,
    p: ViewDep,
) -> MailReplyResponse:
    """Новое письмо с ящика (ADR-044 §8). Гейт mail:view; ящик ∈ scope."""
    return await service.compose(
        scope=scope,
        user_id=p.user_id,
        mailbox_id=mailbox_id,
        payload=payload,
    )


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
    """Удаление тега (ADR-044 §5, ADR-047 §1). Гейт mail:tags; удалить можно ЛЮБОЙ тег."""
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
