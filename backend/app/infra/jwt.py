"""Выпуск и валидация JWT (HS256, 05-security.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import get_settings


class TokenError(Exception):
    """Невалидный/просроченный токен."""


def issue_access_token(username: str) -> tuple[str, int]:
    """Выпускает access-токен. Возвращает (token, expires_in_seconds)."""
    settings = get_settings()
    now = datetime.now(UTC)
    expires_in = settings.jwt_expires_seconds
    payload: dict[str, Any] = {
        "sub": username,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "type": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str) -> str:
    """Валидирует токен и возвращает username (claim `sub`). Иначе TokenError."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Невалидный или просроченный токен") from exc

    if payload.get("type") != "access":
        raise TokenError("Неверный тип токена")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenError("Отсутствует subject в токене")
    return sub
