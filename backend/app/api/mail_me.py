"""Приватные self-эндпоинты почты пользователя (ADR-044 §2, MAJOR-4). Гейт `mail:view`.

`GET/PATCH /api/mail/me/settings` — чтение/изменение opt-out Telegram-уведомлений по
`principal.user_id`. Механизм обязателен: без него после переезда с агрегатора
пользователь не сможет отписаться (регресс). Дефолт (нет строки) = уведомления включены.
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
    """Текущее состояние opt-out. Супер-админ без `uid` → 403 (нет БД-строки)."""
    if principal.user_id is None:
        raise forbidden()
    return await service.get_settings(principal.user_id)


@router.patch("/settings", response_model=MailUserSettingsResponse)
async def update_settings(
    payload: MailUserSettingsUpdateRequest,
    service: MailTelegramServiceDep,
    principal: ViewDep,
) -> MailUserSettingsResponse:
    """Установить opt-out уведомлений (upsert по `principal.user_id`, ADR-044 §2).

    Супер-админ (`.env`) не имеет БД-строки → 403 forbidden.
    """
    if principal.user_id is None:
        raise forbidden()
    return await service.update_settings(
        user_id=principal.user_id, enabled=payload.tg_notifications_enabled
    )
