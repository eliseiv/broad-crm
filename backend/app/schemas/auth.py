"""Схемы аутентификации (04-api.md#auth, ADR-025)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Тело POST /api/auth/login.

    `username` — **идентификатор входа** (логин ИЛИ телеграм-ник, ADR-025; имя поля
    сохранено для совместимости). `password` — **опционален**: для парольного
    пользователя обязателен по смыслу (пустой → 401), для беспарольного игнорируется
    (ответ `password_setup_required`). Длина пароля намеренно не ограничена снизу
    (пустой/отсутствующий трактуется как «без пароля» → 401, а не 400).
    """

    username: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, max_length=256)


class SetPasswordRequest(BaseModel):
    """Тело POST /api/auth/set-password (установка пароля «первого входа», ADR-025).

    `password` required; длина 8–128 валидируется сервисом → 422 unprocessable
    (слабый/короткий пароль), а не 400 (schema-level без границы длины).
    """

    password: str


class LoginResponse(BaseModel):
    """Ответ 200 POST /api/auth/login и POST /api/auth/set-password (ADR-025).

    Дискриминирован по `password_setup_required`:
      - `false` — обычный вход: заполнен `access_token` (`setup_token=None`);
      - `true` — требуется установка пароля: заполнен `setup_token` (`access_token=None`).
    `None`-поля исключаются из тела ответа (`response_model_exclude_none=True` на роуте).
    """

    password_setup_required: bool
    access_token: str | None = None
    setup_token: str | None = None
    token_type: str = "bearer"
    expires_in: int


class TokenResponse(BaseModel):
    """Успешный ответ входа (обычный access-токен). Внутренний хелпер построения
    `LoginResponse` при `password_setup_required=false`."""

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
