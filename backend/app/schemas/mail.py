"""Схемы модуля «Почты» (04-api.md#mail).

Read-through-прокси к внешнему сервису `postapp.store` без хранения (ADR-012,
modules/mail). Внешний DTO проксируется 1:1 в нормативные схемы ниже; поля и типы —
строго по 04-api.md#mail. Ключ `MAIL_API_KEY` в этих схемах не присутствует и в
ответах CRM не возвращается (05-security.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Режим пагинации ленты (04-api.md#mail): `desc` — backward newest-first (основной
# режим страницы), `asc` — keyset вперёд (обратная совместимость). Default эндпоинта
# CRM — `desc`; во внешний API `order` передаётся всегда явно.
MailOrder = Literal["asc", "desc"]

# Тип правила тега (04-api.md#mail, external ADR-0040). Человекочитаемые подписи —
# на стороне frontend (08-design-system.md).
MailTagRuleType = Literal["subject_contains", "body_contains", "sender_contains", "sender_exact"]
# Режим совпадения правил тега: `any` — любое правило, `all` — все.
MailTagMatchMode = Literal["any", "all"]

_PORT_MIN = 1
_PORT_MAX = 65535


class MailAccount(BaseModel):
    """Почтовый аккаунт-получатель (04-api.md#mail)."""

    id: int
    email: str
    display_name: str | None


class MailTag(BaseModel):
    """Тег письма; `color` — HEX для Badge (04-api.md#mail)."""

    id: int
    name: str
    color: str


class MailMessage(BaseModel):
    """Письмо ленты (проекция внешнего DTO, 04-api.md#mail)."""

    id: int
    subject: str | None
    internal_date: datetime
    from_addr: str
    from_name: str | None
    to_addrs: str
    cc_addrs: str | None
    mail_account: MailAccount
    body_text: str
    body_html: str | None
    body_present: bool
    body_truncated: bool
    tags: list[MailTag]


class MailListResponse(BaseModel):
    """Ответ 200 GET /api/mail/messages (единая схема обоих режимов, 04-api.md#mail).

    Заполнен курсор запрошенного режима, второй — `null`:
    - **asc:** `next_since_id` — максимальный `id` в батче (следующий `since_id`);
      `null` для пустого батча (нет писем вперёд). `next_before_id` = `null`.
    - **desc:** `next_before_id` — минимальный `id` в батче (следующий `before_id`,
      догрузка более старых); `null`, если старее нет или батч пуст.
      `next_since_id` = `null`.

    `has_more` — есть ли ещё письма в запрошенном направлении.
    """

    messages: list[MailMessage]
    next_since_id: int | None
    next_before_id: int | None
    has_more: bool


class MailReplyRequest(BaseModel):
    """Тело POST /api/mail/messages/{id}/reply (04-api.md#mail).

    `body` обязателен и непуст (пустой → 422 unprocessable, проверяется в сервисе;
    поэтому без `min_length`, иначе получили бы 400 вместо 422). `to`/`cc`/`subject`
    опциональны: `None` → внешний сервис применяет дефолты (отправитель исходного).
    """

    to: list[str] | None = None
    cc: list[str] | None = None
    subject: str | None = None
    body: str


class MailReplyResponse(BaseModel):
    """Ответ 200 POST /api/mail/messages/{id}/reply (04-api.md#mail)."""

    sent_id: int
    smtp_message_id: str


class MailTeam(BaseModel):
    """Команда (`groups` внешнего сервиса, external ADR-0037; 04-api.md#mail).

    Команда ≠ тег (`MailTag`): теги остаются отдельной сущностью письма.
    """

    id: int
    name: str


class MailMailbox(BaseModel):
    """Почтовый ящик внешнего сервиса (external ADR-0037/ADR-0039; 04-api.md#mail).

    `id` используется как `mail_account_id` в фильтре GET /api/mail/messages и в
    PATCH/DELETE/sync; привязка к команде — через `group_id`; `is_active` — статус.
    Поля статуса синка (`last_synced_at`/`last_sync_error`/`consecutive_failures`) —
    для кружка статуса и диагностики (ADR-0039, аддитивно). Пароли в схему НЕ входят
    и в ответах не возвращаются (05-security.md).

    Поля статуса — **required без дефолтов** (внешний DTO обязан всегда их отдавать —
    mail-агрегатор 04-api-contracts.md §4d-mailboxes/§4f): их отсутствие в ответе =
    регресс контракта интеграции → `ValidationError` → 502, а НЕ тихое «ящик здоров»
    (зелёный кружок при сломанном синке). Значения `last_synced_at`/`last_sync_error`
    могут быть законно `null` (новый ящик) — это допустимое значение, не отсутствие поля.
    """

    id: int
    email: str
    display_name: str | None
    group_id: int | None
    is_active: bool
    last_synced_at: datetime | None
    last_sync_error: str | None
    consecutive_failures: int


class MailTeamsResponse(BaseModel):
    """Ответ 200 GET /api/mail/teams (04-api.md#mail). Список может быть пустым."""

    teams: list[MailTeam]


class MailMailboxesResponse(BaseModel):
    """Ответ 200 GET /api/mail/mailboxes (04-api.md#mail). Список может быть пустым."""

    mailboxes: list[MailMailbox]


class MailMailboxTestRequest(BaseModel):
    """Тело POST /api/mail/mailboxes/test (04-api.md#mail). Пароли — транзитом в агрегатор.

    Не логируются, не возвращаются (05-security.md). `smtp_username`/`smtp_password`
    опц.: `None` → внешний сервис берёт `email`/`password` соответственно.
    """

    email: str
    imap_host: str
    imap_port: int = Field(ge=_PORT_MIN, le=_PORT_MAX)
    imap_ssl: bool
    smtp_host: str
    smtp_port: int = Field(ge=_PORT_MIN, le=_PORT_MAX)
    smtp_ssl: bool
    smtp_starttls: bool
    smtp_username: str | None = None
    password: str
    smtp_password: str | None = None


class MailMailboxTestResponse(BaseModel):
    """Ответ 200 POST /api/mail/mailboxes/test (04-api.md#mail)."""

    imap_ok: bool
    smtp_ok: bool


class MailMailboxCreateRequest(MailMailboxTestRequest):
    """Тело POST /api/mail/mailboxes (04-api.md#mail) = поля `test` + привязка/имя.

    `group_id` (ge=1) — команда (`MailTeam.id`), к которой привязать ящик; `null` —
    без команды. Для не-admin `group_id` обязан ∈ `MailScope.group_ids` (иначе 403).
    """

    display_name: str | None = None
    group_id: int | None = Field(default=None, ge=1)


class MailMailboxUpdateRequest(BaseModel):
    """Тело PATCH /api/mail/mailboxes/{id} (04-api.md#mail). Presence-семантика полей.

    Передаются только изменяемые поля (`model_dump(exclude_unset=True)`). Пароли —
    транзитом (не логируются/не возвращаются). `group_id`: int — сменить команду
    (для не-admin ∈ `MailScope.group_ids`); `null` — снять привязку.
    """

    email: str | None = None
    display_name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=_PORT_MIN, le=_PORT_MAX)
    imap_ssl: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=_PORT_MIN, le=_PORT_MAX)
    smtp_ssl: bool | None = None
    smtp_starttls: bool | None = None
    smtp_username: str | None = None
    password: str | None = None
    smtp_password: str | None = None
    is_active: bool | None = None
    group_id: int | None = Field(default=None, ge=1)


class MailMailboxSyncResponse(BaseModel):
    """Ответ 202 POST /api/mail/mailboxes/{id}/sync (04-api.md#mail)."""

    queued: bool


class MailTagRule(BaseModel):
    """Правило тега (04-api.md#mail, external ADR-0040)."""

    id: int
    type: MailTagRuleType
    pattern: str
    created_at: datetime


class MailTagFull(BaseModel):
    """Полный тег с правилами (вкладка «Теги»; глобальный каталог, 04-api.md#mail)."""

    id: int
    name: str
    color: str
    match_mode: MailTagMatchMode
    is_builtin: bool
    rules: list[MailTagRule]
    created_at: datetime
    updated_at: datetime


class MailTagsResponse(BaseModel):
    """Ответ 200 GET /api/mail/tags (04-api.md#mail). Список может быть пустым."""

    tags: list[MailTagFull]


class MailTagCreateRequest(BaseModel):
    """Тело POST /api/mail/tags (04-api.md#mail). `match_mode` опц., default `any`."""

    name: str
    color: str
    match_mode: MailTagMatchMode = "any"


class MailTagUpdateRequest(BaseModel):
    """Тело PATCH /api/mail/tags/{id} (04-api.md#mail). Все поля опц."""

    name: str | None = None
    color: str | None = None
    match_mode: MailTagMatchMode | None = None


class MailTagRuleCreateRequest(BaseModel):
    """Тело POST /api/mail/tags/{id}/rules (04-api.md#mail)."""

    type: MailTagRuleType
    pattern: str


class MailTagApplyResponse(BaseModel):
    """Ответ 200 POST /api/mail/tags/{id}/apply-to-existing (04-api.md#mail)."""

    applied_count: int


class TeamMailboxItem(BaseModel):
    """Ящик команды для detail-панели /teams (минимальная схема без кредов, 04-api.md#teams)."""

    id: int
    email: str
    display_name: str | None
    is_active: bool


class TeamMailboxesResponse(BaseModel):
    """Ответ 200 GET /api/teams/{id}/mailboxes (04-api.md#teams). Может быть пустым."""

    mailboxes: list[TeamMailboxItem]


__all__ = [
    "MailAccount",
    "MailListResponse",
    "MailMailbox",
    "MailMailboxCreateRequest",
    "MailMailboxSyncResponse",
    "MailMailboxTestRequest",
    "MailMailboxTestResponse",
    "MailMailboxUpdateRequest",
    "MailMailboxesResponse",
    "MailMessage",
    "MailOrder",
    "MailReplyRequest",
    "MailReplyResponse",
    "MailTag",
    "MailTagApplyResponse",
    "MailTagCreateRequest",
    "MailTagFull",
    "MailTagMatchMode",
    "MailTagRule",
    "MailTagRuleCreateRequest",
    "MailTagRuleType",
    "MailTagUpdateRequest",
    "MailTagsResponse",
    "MailTeam",
    "MailTeamsResponse",
    "TeamMailboxItem",
    "TeamMailboxesResponse",
]
