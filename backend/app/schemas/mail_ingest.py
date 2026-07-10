"""Схемы push-контракта агрегатор→CRM: `/api/mail/ingest`, `/api/mail/mailbox-status`.

Контракт — ADR-044 §3 (зеркалит mail-агрегатор `ADR-0043` §2). Машинные эндпоинты
(HMAC, без JWT). Вложения не передаются. Батч валидируется в сервисе против
`MAIL_INGEST_MAX_BATCH` (400 validation_error при превышении/пустом).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MailIngestMessage(BaseModel):
    """Одно письмо в батче push'а (ADR-044 §3, таблица MailIngestMessage)."""

    mail_account_id: int
    uidvalidity: int
    uid: int
    message_id_header: str | None = None
    subject: str | None = None
    from_addr: str
    from_name: str | None = None
    to_addrs: str
    cc_addrs: str | None = None
    internal_date: datetime
    body_text: str
    body_html: str | None = None
    in_reply_to: str | None = None
    refs_header: str | None = None


class MailIngestRequest(BaseModel):
    """Тело `POST /api/mail/ingest`: батч писем (1..MAIL_INGEST_MAX_BATCH)."""

    messages: list[MailIngestMessage]


class MailIngestResponse(BaseModel):
    """Ответ 200 `POST /api/mail/ingest` (ADR-044 §3)."""

    accepted: int
    duplicate: int
    unknown_mailbox: int


class MailboxStatusRequest(BaseModel):
    """Тело `POST /api/mail/mailbox-status`: зеркало статуса синка ящика (ADR-044 §3)."""

    mail_account_id: int
    is_active: bool
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    consecutive_failures: int = 0


class MailboxStatusResponse(BaseModel):
    """Ответ 200 `POST /api/mail/mailbox-status`.

    `updated=false` — неизвестный `mail_account_id` (no-op, аномалия TD-041); батч/запрос
    НЕ отклоняется.
    """

    updated: bool


class MailOauthIngestRequest(BaseModel):
    """Тело `POST /api/mail/oauth/ingest`: уведомление о созданном/переподключённом
    Outlook-ящике (ADR-045 §3).

    `crm_state` — непрозрачный подписанный CRM-токен (несёт `team_id`/`initiator`/`exp`).
    `mail_account_id` — id ящика, присвоенный агрегатором (ключ каталожной записи CRM).
    Токены/креды Outlook в CRM НЕ передаются и не хранятся (живут в агрегаторе).
    """

    crm_state: str
    mail_account_id: int
    email: str
    display_name: str | None = None
    is_active: bool


class MailOauthIngestResponse(BaseModel):
    """Ответ 200 `POST /api/mail/oauth/ingest` (ADR-045 §3)."""

    ok: bool
