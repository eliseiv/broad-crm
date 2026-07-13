"""Схемы модуля «Почты» (ADR-044).

CRM — система-запись писем/тегов/каталога ящиков (разворот «без хранения» ADR-012/038).
Лента/теги/ящики читаются из БД CRM; создание/правка/удаление ящика и reply транзитом
делегируются агрегатору (креды не хранятся в CRM). Поля и типы — строго по ADR-044
(§2/§4/§5/§8). Пароли/креды в схемы ответов не входят и в ответах не возвращаются
(05-security.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Тип правила тега (ADR-044 §5). Человекочитаемые подписи — на стороне frontend.
MailTagRuleType = Literal["subject_contains", "body_contains", "sender_contains", "sender_exact"]
# Режим совпадения правил тега: `any` — любое правило, `all` — все.
MailTagMatchMode = Literal["any", "all"]

_PORT_MIN = 1
_PORT_MAX = 65535


# --- Лента писем (чтение из БД CRM) -----------------------------------------


class MailAccountRef(BaseModel):
    """Ссылка на ящик-владелец письма (проекция каталога, ADR-044 §2)."""

    id: int
    email: str
    display_name: str | None


class MailTag(BaseModel):
    """Тег письма для ленты; `color` — HEX для Badge (ADR-044 §5)."""

    id: uuid.UUID
    name: str
    color: str


class MailMessage(BaseModel):
    """Письмо ленты (проекция `mail_messages` + ящик + теги, ADR-044 §2).

    Схема ОДНА и для ленты, и для детали (отдельного `GET /messages/{id}` нет), поэтому
    `is_unread` приходит в обоих местах одним полем.

    `is_unread` (ADR-050 §2.2) — ЛИЧНОЕ производное: `true` ⇔ для ТЕКУЩЕГО принципала нет
    строки `mail_message_reads(user_id, message_id)`. Один и тот же `id` письма у разных
    пользователей даёт разные значения. Супер-админ из `.env` (нет строки в `users`) →
    всегда `false`. `read_at` наружу не отдаётся.
    """

    id: int
    subject: str | None
    internal_date: datetime
    from_addr: str
    from_name: str | None
    to_addrs: str
    cc_addrs: str | None
    mail_account: MailAccountRef
    body_text: str
    body_html: str | None
    body_present: bool
    body_truncated: bool
    is_unread: bool
    tags: list[MailTag]


class MailListResponse(BaseModel):
    """Ответ 200 GET /api/mail/messages (компаундный keyset, ADR-044 §2).

    Порядок — `internal_date DESC, id DESC` (истинная дата письма, а не порядок push'а).
    `next_cursor` — opaque-токен пары `(internal_date, id)` последнего элемента страницы
    для догрузки более старых (передаётся обратно как `before`); `null` — старее нет.
    """

    messages: list[MailMessage]
    next_cursor: str | None


# --- Reply (отправка через агрегатор) ---------------------------------------


class MailReplyRequest(BaseModel):
    """Тело POST /api/mail/messages/{id}/reply (ADR-044 §8).

    `body` обязателен и непуст (пустой/> 1 MiB → 422 unprocessable, проверяется в
    сервисе; поэтому без `min_length`/`max_length`, иначе получили бы 400 вместо 422).
    `to`/`cc`/`subject` опциональны: `None` → сервер применяет дефолты (адрес/тема
    исходного письма). Нормы (≤100 адресов, e-mail regex, subject ≤998) — в сервисе.
    """

    to: list[str] | None = None
    cc: list[str] | None = None
    subject: str | None = None
    body: str


class MailReplyResponse(BaseModel):
    """Ответ 200 POST /api/mail/messages/{id}/reply (ADR-044 §8)."""

    sent_id: int
    smtp_message_id: str


# --- Каталог ящиков (чтение из БД CRM; write — транзит в агрегатор) ----------


class MailMailbox(BaseModel):
    """Почтовый ящик из каталога CRM `mail_accounts` (ADR-044 §2/§4, ADR-047 §3).

    `id` = id ящика в агрегаторе; `team_id` — команда-владелец (per-mailbox, `null` —
    unassigned). Поля статуса синка зеркалятся из агрегатора status-каналом. Пароли в
    схему НЕ входят и в ответах не возвращаются (05-security.md).

    `number`/`app_name` — «Номер»/«Приложение»; `display_name` — ПРОИЗВОДНОЕ (read-only
    для клиента) имя `"<number> <app_name>"`, считает сервер (ADR-047 §3.3).
    """

    id: int
    email: str
    number: str | None
    app_name: str | None
    display_name: str | None
    team_id: uuid.UUID | None
    is_active: bool
    last_synced_at: datetime | None
    last_sync_error: str | None
    consecutive_failures: int


class MailMailboxesResponse(BaseModel):
    """Ответ 200 GET /api/mail/mailboxes (ADR-044 §4). Список может быть пустым."""

    mailboxes: list[MailMailbox]


class MailMailboxTestRequest(BaseModel):
    """Тело POST /api/mail/mailboxes/test (ADR-044 §4). Креды — транзитом в агрегатор.

    Не логируются, не возвращаются (05-security.md). `smtp_username`/`smtp_password`
    опц.: `None` → агрегатор берёт `email`/`password` соответственно.
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
    """Ответ 200 POST /api/mail/mailboxes/test (ADR-044 §4)."""

    imap_ok: bool
    smtp_ok: bool


class MailMailboxCreateRequest(MailMailboxTestRequest):
    """Тело POST /api/mail/mailboxes (ADR-044 §4, ADR-047 §3) = поля `test` + привязка/имя.

    `team_id` (uuid) — команда-владелец; `null` — без команды (unassigned, только
    admin-уровень). Для не-admin `team_id` обязан ∈ `MailScope.team_ids` (иначе 403).
    Креды транзитом в агрегатор; в каталог CRM сохраняется строка без кредов.

    `display_name` в запросе НЕ принимается (ADR-047 §3.2) — сервер вычисляет его из
    `number`/`app_name`.
    """

    number: str | None = None
    app_name: str | None = None
    team_id: uuid.UUID | None = None


class MailMailboxUpdateRequest(BaseModel):
    """Тело PATCH /api/mail/mailboxes/{id} (ADR-044 §4, ADR-047 §3). Presence-семантика.

    Передаются только изменяемые поля (`model_fields_set`/`exclude_unset`). Креды —
    транзитом в агрегатор. `team_id` (перенос между командами) — только admin-уровень
    (`MailScope.sees_all_teams`); `null` — снять привязку (unassigned).

    `display_name` в запросе НЕ принимается (ADR-047 §3.2): при изменении `number` или
    `app_name` сервер пересчитывает его сам и отправляет в агрегатор.
    """

    email: str | None = None
    number: str | None = None
    app_name: str | None = None
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
    team_id: uuid.UUID | None = None


class MailMailboxSyncResponse(BaseModel):
    """Ответ 202 POST /api/mail/mailboxes/{id}/sync (ADR-044 §4)."""

    queued: bool


# --- Outlook OAuth (headless, ADR-045) --------------------------------------


class MailOauthAuthorizeRequest(BaseModel):
    """Тело POST /api/mail/mailboxes/oauth/authorize (ADR-045 §3).

    `team_id` (uuid) — команда-владелец будущего Outlook-ящика; `null` — без команды
    (unassigned, только admin-уровень). Авторизация — как создание ящика (ADR-044 §4):
    для не-admin `team_id` обязан ∈ `MailScope.team_ids` (иначе 403).
    """

    team_id: uuid.UUID | None = None


class MailOauthAuthorizeResponse(BaseModel):
    """Ответ 200 POST /api/mail/mailboxes/oauth/authorize (ADR-045 §3).

    `authorize_url` — Microsoft authorize URL (минтит агрегатор); CRM показывает его
    пользователю для открытия в нужном профиле OctoBrowser (не auto-redirect, §5).
    """

    authorize_url: str


# --- Теги (глобальный админский каталог, чтение/запись из БД CRM) ------------


class MailTagRule(BaseModel):
    """Правило тега (ADR-044 §5)."""

    id: uuid.UUID
    type: MailTagRuleType
    pattern: str
    created_at: datetime


class MailTagFull(BaseModel):
    """Полный тег с правилами (вкладка «Теги»; глобальный каталог, ADR-044 §5).

    Поля `is_builtin` НЕТ (ADR-047 §1): признак «встроенный тег» упразднён, колонка
    дропнута миграцией `0023`, удалить можно ЛЮБОЙ тег.
    """

    id: uuid.UUID
    name: str
    color: str
    match_mode: MailTagMatchMode
    rules: list[MailTagRule]
    created_at: datetime
    updated_at: datetime


class MailTagsResponse(BaseModel):
    """Ответ 200 GET /api/mail/tags (ADR-044 §5). Список может быть пустым."""

    tags: list[MailTagFull]


class MailTagCreateRequest(BaseModel):
    """Тело POST /api/mail/tags (ADR-044 §5). `match_mode` опц., default `any`."""

    name: str = Field(min_length=1, max_length=64)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    match_mode: MailTagMatchMode = "any"


class MailTagUpdateRequest(BaseModel):
    """Тело PATCH /api/mail/tags/{id} (ADR-044 §5). Все поля опц."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    match_mode: MailTagMatchMode | None = None


class MailTagRuleCreateRequest(BaseModel):
    """Тело POST /api/mail/tags/{id}/rules (ADR-044 §5)."""

    type: MailTagRuleType
    pattern: str = Field(min_length=1, max_length=256)


class MailTagApplyResponse(BaseModel):
    """Ответ 200 POST /api/mail/tags/{id}/apply-to-existing (ADR-044 §5)."""

    applied_count: int


# --- Ящики команды для detail-панели /teams ---------------------------------


class TeamMailboxItem(BaseModel):
    """Ящик команды для detail-панели /teams (минимальная схема без кредов, ADR-044 §4).

    Расширена `number`/`app_name` («Номер»/«Приложение», ADR-048 §2). Креды/хосты и
    статус синка (`last_synced_at`/`last_sync_error`/`consecutive_failures`) в схему
    НЕ входят — сужение ADR-044 §4 сохраняется. `display_name` остаётся в контракте
    (производное имя), но в строке detail-панели не рендерится (ADR-048 §2/§3).
    """

    id: int
    email: str
    number: str | None
    app_name: str | None
    display_name: str | None
    is_active: bool


class TeamMailboxesResponse(BaseModel):
    """Ответ 200 GET /api/teams/{id}/mailboxes (ADR-044 §4). Может быть пустым."""

    mailboxes: list[TeamMailboxItem]


__all__ = [
    "MailAccountRef",
    "MailListResponse",
    "MailMailbox",
    "MailMailboxCreateRequest",
    "MailMailboxSyncResponse",
    "MailMailboxTestRequest",
    "MailMailboxTestResponse",
    "MailMailboxUpdateRequest",
    "MailMailboxesResponse",
    "MailMessage",
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
    "TeamMailboxItem",
    "TeamMailboxesResponse",
]
