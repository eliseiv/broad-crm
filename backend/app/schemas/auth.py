"""Схемы аутентификации (04-api.md#auth)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Тело POST /api/auth/login."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    """Ответ 200 POST /api/auth/login."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    """Ответ 200 GET /api/auth/me."""

    username: str
