"""Приватные self-эндпоинты почты пользователя (ADR-044 §2, MAJOR-4). Гейт `mail:view`.

`GET/PATCH /api/mail/me/settings` — чтение/изменение opt-out Telegram-уведомлений по
`principal.user_id`. Механизм обязателен: без него после переезда с агрегатора
пользователь не сможет отписаться (регресс). Дефолт (нет строки) = уведомления включены.

**Супер-админ (`.env`) → 403 forbidden (ADR-051 §1.6).** Основание — SECURITY, а не
«нет БД-строки» (строка-якорь у него теперь есть, и личное состояние — прочитанность
писем — ему доступно): bootstrap-учётке **запрещена Telegram-привязка**, иначе владение
Telegram-аккаунтом стало бы беспарольным, неотзываемым из UI путём к admin-уровню в
обход `ADMIN_PASSWORD`. Уведомления ей не доставляются ⇒ персональная настройка
бессодержательна. Условие гейта — `principal.is_superadmin` (прежнее `user_id is None`
после ADR-051 §1.2 недостижимо).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import MailTelegramServiceDep, Principal, require
from app.errors import forbidden
from app.schemas.mail_telegram import (
    MailUserSettingsResponse,
    MailUserSettingsUpdateRequest,
)

router = APIRouter(prefix="/mail/me", tags=["mail-me"])

ViewDep = Annotated[Principal, Depends(require("mail", "view"))]


@router.get("/settings", response_model=MailUserSettingsResponse)
async def get_settings(
    service: MailTelegramServiceDep,
    principal: ViewDep,
) -> MailUserSettingsResponse:
    """Текущее состояние opt-out. Супер-админ (`.env`) → 403 forbidden (ADR-051 §1.6)."""
    if principal.is_superadmin:
        raise forbidden()
    return await service.get_settings(principal.user_id)


@router.patch("/settings", response_model=MailUserSettingsResponse)
async def update_settings(
    payload: MailUserSettingsUpdateRequest,
    service: MailTelegramServiceDep,
    principal: ViewDep,
) -> MailUserSettingsResponse:
    """Установить opt-out уведомлений (upsert по `principal.user_id`, ADR-044 §2).

    Супер-админ (`.env`) → 403 forbidden (ADR-051 §1.6).
    """
    if principal.is_superadmin:
        raise forbidden()
    return await service.update_settings(
        user_id=principal.user_id, enabled=payload.tg_notifications_enabled
    )
