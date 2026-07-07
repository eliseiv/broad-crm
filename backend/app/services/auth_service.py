"""Бизнес-логика аутентификации (modules/auth, 05-security.md, ADR-021).

Две ветки логина (нормативно): СПЕРВА `.env`-супер-админ (constant-time plaintext),
ЗАТЕМ БД-пользователь (bcrypt `verify_password` + `is_active`). Неудача любой ветки
→ единое `401 invalid_credentials` (user-enumeration не раскрывается).
"""

from __future__ import annotations

import secrets

from app.config import Settings
from app.errors import invalid_credentials, rate_limited
from app.infra.jwt import issue_access_token
from app.infra.passwords import verify_password
from app.infra.rate_limit import InMemoryRateLimiter
from app.logging import get_logger
from app.repositories.user_repository import UserRepository
from app.schemas.auth import TokenResponse

logger = get_logger(__name__)


class AuthService:
    """Проверка кредов (супер-админ из .env ИЛИ БД-пользователь) и выпуск JWT."""

    def __init__(
        self,
        *,
        settings: Settings,
        rate_limiter: InMemoryRateLimiter,
        user_repository: UserRepository,
    ) -> None:
        self._settings = settings
        self._rate_limiter = rate_limiter
        self._users = user_repository

    async def login(self, *, username: str, password: str, client_ip: str) -> TokenResponse:
        """Rate-limit по IP; сперва супер-админ (constant-time), затем БД-пользователь."""
        if not self._rate_limiter.is_allowed(client_ip):
            logger.warning("login_rate_limited", client_ip=client_ip)
            raise rate_limited()

        # 1) Супер-админ (.env). Оба сравнения выполняются всегда (без раннего
        # возврата), чтобы не было timing-разницы логин↔пароль (05-security.md).
        # Сравниваем UTF-8 БАЙТЫ: secrets.compare_digest на не-ASCII `str` бросает
        # TypeError → кириллический БД-пользователь («Никита») получал бы 500 вместо
        # 401 (ADR-021 Cyrillic login). encode применяется к обеим сторонам.
        user_ok = secrets.compare_digest(
            username.encode("utf-8"), self._settings.admin_user.encode("utf-8")
        )
        password_ok = secrets.compare_digest(
            password.encode("utf-8"), self._settings.admin_password.encode("utf-8")
        )
        if user_ok and password_ok:
            token, expires_in = issue_access_token(
                sub=self._settings.admin_user, role="admin", superadmin=True
            )
            logger.info("login_succeeded", username=self._settings.admin_user, superadmin=True)
            return TokenResponse(access_token=token, token_type="bearer", expires_in=expires_in)

        # 2) БД-пользователь: username + verify_password (bcrypt) + is_active.
        user = await self._users.get_by_username(username)
        if user is not None and user.is_active and verify_password(password, user.password_hash):
            token, expires_in = issue_access_token(
                sub=user.username,
                role=user.role.name,
                superadmin=False,
                uid=str(user.id),
            )
            logger.info("login_succeeded", username=user.username, superadmin=False)
            return TokenResponse(access_token=token, token_type="bearer", expires_in=expires_in)

        logger.info("login_failed", client_ip=client_ip)
        raise invalid_credentials()
