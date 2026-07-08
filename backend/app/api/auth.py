"""Роутер аутентификации (04-api.md#auth, ADR-025)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AuthServiceDep, ClientIp, PrincipalDep, SetupPrincipalDep
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
async def me(principal: PrincipalDep) -> MeResponse:
    """Профиль текущей сессии + права принципала для UI-гейтинга (ADR-021)."""
    return MeResponse(
        username=principal.username,
        role=principal.role,
        is_superadmin=principal.is_superadmin,
        permissions=principal.permissions,
    )
