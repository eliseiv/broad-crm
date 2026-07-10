"""Схемы Telegram-слоя почты (ADR-044 §6/§7): Mini App SSO + opt-out настройки.

`MailTelegramAuthRequest`/`Response` — беспарольный вход Mini App `/tg/mail` по
`initData` (выдаёт CRM access-JWT, как SMS). `MailUserSettingsUpdateRequest`/`Response`
— opt-out уведомлений (`PATCH /api/mail/me/settings`, MAJOR-4).
"""

from __future__ import annotations

from pydantic import BaseModel


class MailTelegramAuthRequest(BaseModel):
    """Тело `POST /api/mail/telegram/auth` (Mini App SSO, HMAC initData)."""

    init_data: str


class MailTelegramAuthResponse(BaseModel):
    """Ответ 200 `POST /api/mail/telegram/auth` — беспарольный Telegram-SSO (ADR-044 §7).

    Выдаёт CRM access-JWT (как `POST /api/auth/login`): `access_token` c `sub`=
    `users.username`, `uid`/`role`/`superadmin:false`; `expires_in` — TTL access-токена
    в секундах; `linked` всегда `true` при успехе.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    telegram_user_id: int
    linked: bool


class MailUserSettingsUpdateRequest(BaseModel):
    """Тело `PATCH /api/mail/me/settings` (opt-out уведомлений, ADR-044 §2)."""

    tg_notifications_enabled: bool


class MailUserSettingsResponse(BaseModel):
    """Ответ 200 `PATCH/GET /api/mail/me/settings` — текущее состояние opt-out."""

    tg_notifications_enabled: bool
