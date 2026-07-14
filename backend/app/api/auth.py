"""Роутер аутентификации (04-api.md#auth, ADR-025)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import (
    AuthServiceDep,
    ClientIp,
    DbSession,
    PrincipalDep,
    SetupPrincipalDep,
    principal_sees_all_mail_teams,
    principal_sees_all_sms_teams,
    resolve_channel_scope,
)
from app.domain.channels import CHANNEL_MAIL, CHANNEL_SMS
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse, SetPasswordRequest

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse, response_model_exclude_none=True)
async def login(
    payload: LoginRequest, service: AuthServiceDep, client_ip: ClientIp
) -> LoginResponse:
    """Проверяет идентификатор (+пароль) и выдаёт access-token ИЛИ setup-token.

    Идентификатор = Логин ИЛИ Телеграм; беспарольный пользователь → `password_setup_required`.
    """
    return await service.login(
        username=payload.username, password=payload.password, client_ip=client_ip
    )


@router.post("/set-password", response_model=LoginResponse, response_model_exclude_none=True)
async def set_password(
    payload: SetPasswordRequest, service: AuthServiceDep, setup: SetupPrincipalDep
) -> LoginResponse:
    """Устанавливает пароль «первого входа» беспарольному пользователю (ADR-025).

    Auth — Bearer setup-token (`type:"pwd_setup"`). Успех → обычный access-token.
    """
    return await service.set_password(uid=setup.uid, password=payload.password)


@router.get("/me", response_model=MeResponse)
async def me(principal: PrincipalDep, session: DbSession) -> MeResponse:
    """Профиль сессии + права + **scope каналов** для UI-гейтинга (ADR-021, ADR-055 §5.1).

    `mail_teams`/`sms_teams` — ЭФФЕКТИВНЫЙ scope канала (не-админ: базовые ∪ добавка;
    admin-уровень: ВСЕ команды системы), `*_includes_unassigned` — «Без команды» канала
    (при admin-уровне → `true`). `/me` — ЕДИНСТВЕННЫЙ источник опций команд канала на
    клиенте (в т.ч. в Mini App, где `GET /api/teams` запрещён; §6.2/§6.3).
    """
    mail_teams, mail_includes_unassigned = await resolve_channel_scope(
        session, principal, CHANNEL_MAIL
    )
    sms_teams, sms_includes_unassigned = await resolve_channel_scope(
        session, principal, CHANNEL_SMS
    )
    return MeResponse(
        username=principal.username,
        role=principal.role,
        is_superadmin=principal.is_superadmin,
        permissions=principal.permissions,
        sees_all_sms_teams=principal_sees_all_sms_teams(principal),
        sees_all_mail_teams=principal_sees_all_mail_teams(principal),
        mail_teams=mail_teams,
        sms_teams=sms_teams,
        mail_includes_unassigned=mail_includes_unassigned,
        sms_includes_unassigned=sms_includes_unassigned,
    )
