"""Выпуск и валидация JWT (HS256, 05-security.md, ADR-021).

Claim'ы (побуквенно, 05-security.md#jwt): `sub` (username), `role`, `superadmin`
(bool), `uid` (uuid — ТОЛЬКО у БД-пользователя, отсутствует у супер-админа), `iat`,
`exp`, `type:"access"`. Токен без `superadmin`-claim и без `uid` (легаси до
Спринта 3) → `TokenError` (enforcement трактует как 401, повторный вход).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import get_settings


class TokenError(Exception):
    """Невалидный/просроченный/легаси токен."""


@dataclass(frozen=True)
class AccessTokenClaims:
    """Полезная нагрузка access-токена (RBAC-claim'ы, ADR-021)."""

    sub: str
    role: str
    superadmin: bool
    uid: str | None


@dataclass(frozen=True)
class SetupTokenClaims:
    """Полезная нагрузка limited-scope setup-токена «первого входа» (ADR-025).

    `type:"pwd_setup"`, несёт `sub`/`uid`, БЕЗ `role`/`superadmin`/прав. Принимается
    ТОЛЬКО `POST /api/auth/set-password`; `get_current_principal` его отвергает.
    """

    sub: str
    uid: str


def issue_access_token(
    *, sub: str, role: str, superadmin: bool, uid: str | None = None
) -> tuple[str, int]:
    """Выпускает access-токен с RBAC-claim'ами. Возвращает (token, expires_in_sec).

    `uid` включается в payload ТОЛЬКО для БД-пользователя (у супер-админа отсутствует).
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires_in = settings.jwt_expires_seconds
    payload: dict[str, Any] = {
        "sub": sub,
        "role": role,
        "superadmin": superadmin,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "type": "access",
    }
    if uid is not None:
        payload["uid"] = uid
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def issue_setup_token(*, sub: str, uid: str) -> tuple[str, int]:
    """Выпускает limited-scope setup-токен «первого входа» (ADR-025).

    `type:"pwd_setup"`, claim'ы `sub`/`uid`/`iat`/`exp`, БЕЗ `role`/`superadmin`/прав.
    TTL — `PWD_SETUP_TOKEN_EXPIRES_MIN` (default 10 мин). Возвращает (token, expires_in_sec).
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expires_in = settings.pwd_setup_token_expires_min * 60
    payload: dict[str, Any] = {
        "sub": sub,
        "uid": uid,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
        "type": "pwd_setup",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_setup_token(token: str) -> SetupTokenClaims:
    """Валидирует setup-токен (`type:"pwd_setup"`) и возвращает `sub`/`uid`.

    Любой другой тип (в т.ч. `access`) → `TokenError` (set-password не принимает
    обычный access-token). Просроченный/невалидный → `TokenError`.
    """
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

    if payload.get("type") != "pwd_setup":
        raise TokenError("Неверный тип токена")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenError("Отсутствует subject в токене")

    uid = payload.get("uid")
    if not isinstance(uid, str) or not uid:
        raise TokenError("Отсутствует uid в setup-токене")

    return SetupTokenClaims(sub=sub, uid=uid)


def decode_access_token(token: str) -> AccessTokenClaims:
    """Валидирует токен и возвращает RBAC-claim'ы. Иначе `TokenError`.

    Легаси-токен (без `role`/`superadmin`) → `TokenError` (повторный вход, ADR-021).
    """
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

    role = payload.get("role")
    if not isinstance(role, str) or not role:
        raise TokenError("Отсутствует role в токене (легаси токен)")

    superadmin = payload.get("superadmin")
    if not isinstance(superadmin, bool):
        raise TokenError("Отсутствует superadmin в токене (легаси токен)")

    uid = payload.get("uid")
    if uid is not None and not isinstance(uid, str):
        raise TokenError("Некорректный uid в токене")

    return AccessTokenClaims(sub=sub, role=role, superadmin=superadmin, uid=uid)


__all__ = [
    "AccessTokenClaims",
    "SetupTokenClaims",
    "TokenError",
    "decode_access_token",
    "decode_setup_token",
    "issue_access_token",
    "issue_setup_token",
]
