"""Роутер аутентификации (04-api.md#auth)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AuthServiceDep, ClientIp, PrincipalDep
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, service: AuthServiceDep, client_ip: ClientIp
) -> TokenResponse:
    """Проверяет креды (супер-админ .env ИЛИ БД-пользователь) и выдаёт JWT (24 ч)."""
    return await service.login(
        username=payload.username, password=payload.password, client_ip=client_ip
    )


@router.get("/me", response_model=MeResponse)
async def me(principal: PrincipalDep) -> MeResponse:
    """Профиль текущей сессии + права принципала для UI-гейтинга (ADR-021)."""
    return MeResponse(
        username=principal.username,
        role=principal.role,
        is_superadmin=principal.is_superadmin,
        permissions=principal.permissions,
    )
