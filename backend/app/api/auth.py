"""Роутер аутентификации (04-api.md#auth)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import AuthServiceDep, ClientIp, CurrentUser
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, service: AuthServiceDep, client_ip: ClientIp
) -> TokenResponse:
    """Проверяет креды админа и выдаёт JWT (HS256, TTL 24ч (1440 мин))."""
    return service.login(username=payload.username, password=payload.password, client_ip=client_ip)


@router.get("/me", response_model=MeResponse)
async def me(current_user: CurrentUser) -> MeResponse:
    """Возвращает профиль текущей сессии (валидирует JWT)."""
    return MeResponse(username=current_user)
