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
    """Ответ 200 GET /api/auth/me (профиль + права принципала, ADR-021).

    `permissions` — производное для UI-гейтинга (для супер-админа — полный каталог).
    Безопасность обеспечивается сервером (403), UI-гейтинг — только UX.
    """

    username: str
    role: str
    is_superadmin: bool
    permissions: dict[str, list[str]]
