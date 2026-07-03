"""Схемы модуля «Почты» (04-api.md#mail).

Read-through-прокси к внешнему сервису `postapp.store` без хранения (ADR-012,
modules/mail). Внешний DTO проксируется 1:1 в нормативные схемы ниже; поля и типы —
строго по 04-api.md#mail. Ключ `MAIL_API_KEY` в этих схемах не присутствует и в
ответах CRM не возвращается (05-security.md).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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
    """Ответ 200 GET /api/mail/messages (keyset вперёд, 04-api.md#mail).

    `next_since_id` — максимальный `id` в батче (следующий `since_id`); `null` для
    пустого батча (нет писем вперёд). `has_more` — есть ли ещё письма.
    """

    messages: list[MailMessage]
    next_since_id: int | None
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


__all__ = [
    "MailAccount",
    "MailListResponse",
    "MailMessage",
    "MailReplyRequest",
    "MailReplyResponse",
    "MailTag",
]
