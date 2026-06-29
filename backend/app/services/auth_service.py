"""Бизнес-логика аутентификации администратора (modules/auth, 05-security.md)."""

from __future__ import annotations

import secrets

from app.config import Settings
from app.errors import invalid_credentials, rate_limited
from app.infra.jwt import issue_access_token
from app.infra.rate_limit import InMemoryRateLimiter
from app.logging import get_logger
from app.schemas.auth import TokenResponse

logger = get_logger(__name__)


class AuthService:
    """Проверка кредов админа из .env и выпуск JWT."""

    def __init__(self, settings: Settings, rate_limiter: InMemoryRateLimiter) -> None:
        self._settings = settings
        self._rate_limiter = rate_limiter

    def login(self, *, username: str, password: str, client_ip: str) -> TokenResponse:
        """Constant-time проверка логина/пароля; rate-limit по IP; выпуск JWT."""
        if not self._rate_limiter.is_allowed(client_ip):
            logger.warning("login_rate_limited", client_ip=client_ip)
            raise rate_limited()

        # Оба сравнения выполняются всегда (без раннего возврата), чтобы не было
        # timing-разницы между неверным логином и неверным паролем (05-security.md).
        user_ok = secrets.compare_digest(username, self._settings.admin_user)
        password_ok = secrets.compare_digest(password, self._settings.admin_password)

        if not (user_ok and password_ok):
            logger.info("login_failed", client_ip=client_ip)
            raise invalid_credentials()

        token, expires_in = issue_access_token(self._settings.admin_user)
        logger.info("login_succeeded", username=self._settings.admin_user)
        return TokenResponse(access_token=token, token_type="bearer", expires_in=expires_in)
