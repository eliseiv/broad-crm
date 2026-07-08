"""Бизнес-логика аутентификации (modules/auth, 05-security.md, ADR-021, ADR-025).

Ветки логина (нормативно): СПЕРВА `.env`-супер-админ (constant-time plaintext, всегда
парольный), ЗАТЕМ БД-пользователь — идентификатор сопоставляется с `username` точно,
иначе с нормализованным `telegram` (вход по Логину ИЛИ Телеграму). Для БД-пользователя:
парольный (`password_hash IS NOT NULL`) → bcrypt-проверка → access-token; беспарольный
(`password_hash IS NULL`) → limited-scope setup-token + `password_setup_required:true`.
Неудача парольной ветки → единое `401 invalid_credentials`. Установка пароля «первого
входа» — `set_password` (по setup-token uid). Все пароли БД — только bcrypt-хэш.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from app.config import Settings
from app.domain.telegram import normalize_telegram
from app.errors import (
    invalid_credentials,
    password_already_set,
    rate_limited,
    unauthorized,
    unprocessable,
)
from app.infra.jwt import issue_access_token, issue_setup_token
from app.infra.passwords import hash_password, verify_password
from app.infra.rate_limit import InMemoryRateLimiter
from app.logging import get_logger
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginResponse

logger = get_logger(__name__)

# Политика пароля БД-пользователя (05-security.md): 8–128 символов.
_PASSWORD_MIN_LEN = 8
_PASSWORD_MAX_LEN = 128


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

    async def login(self, *, username: str, password: str | None, client_ip: str) -> LoginResponse:
        """Rate-limit по IP; супер-админ (constant-time), затем БД-пользователь.

        Возвращает `LoginResponse` (дискриминирован по `password_setup_required`):
        успех — access-token; беспарольный пользователь — setup-token (первый вход).
        """
        if not self._rate_limiter.is_allowed(client_ip):
            logger.warning("login_rate_limited", client_ip=client_ip)
            raise rate_limited()

        # 1) Супер-админ (.env). Оба сравнения выполняются всегда (без раннего
        # возврата), чтобы не было timing-разницы логин↔пароль (05-security.md).
        # Сравниваем UTF-8 БАЙТЫ: secrets.compare_digest на не-ASCII `str` бросает
        # TypeError → кириллический БД-пользователь получал бы 500 вместо 401.
        candidate_password = password or ""
        user_ok = secrets.compare_digest(
            username.encode("utf-8"), self._settings.admin_user.encode("utf-8")
        )
        password_ok = secrets.compare_digest(
            candidate_password.encode("utf-8"), self._settings.admin_password.encode("utf-8")
        )
        if user_ok and password_ok:
            token, expires_in = issue_access_token(
                sub=self._settings.admin_user, role="admin", superadmin=True
            )
            logger.info("login_succeeded", username=self._settings.admin_user, superadmin=True)
            return LoginResponse(
                password_setup_required=False,
                access_token=token,
                token_type="bearer",
                expires_in=expires_in,
            )

        # 2) БД-пользователь: идентификатор = username точно, иначе нормализованный telegram.
        user = await self._resolve_db_user(username)
        if user is not None and user.is_active:
            if user.password_hash is not None:
                # Парольный: bcrypt-проверка.
                if verify_password(candidate_password, user.password_hash):
                    # Первый успешный вход: метка проставляется идемпотентно (ADR-028).
                    if user.first_login_at is None:
                        user.first_login_at = datetime.now(UTC)
                        await self._users.session.commit()
                    token, expires_in = issue_access_token(
                        sub=user.username,
                        role=user.role.name,
                        superadmin=False,
                        uid=str(user.id),
                    )
                    logger.info("login_succeeded", username=user.username, superadmin=False)
                    return LoginResponse(
                        password_setup_required=False,
                        access_token=token,
                        token_type="bearer",
                        expires_in=expires_in,
                    )
            else:
                # Беспарольный: вход не выполняется — setup-token (первый вход, ADR-025).
                setup_token, expires_in = issue_setup_token(sub=user.username, uid=str(user.id))
                logger.info("login_password_setup_required", username=user.username)
                return LoginResponse(
                    password_setup_required=True,
                    setup_token=setup_token,
                    token_type="bearer",
                    expires_in=expires_in,
                )

        logger.info("login_failed", client_ip=client_ip)
        raise invalid_credentials()

    async def set_password(self, *, uid: str, password: str) -> LoginResponse:
        """Устанавливает пароль «первого входа» беспарольному пользователю (ADR-025).

        Прецеденция: слабый/короткий пароль → 422; пользователь не найден/деактивирован →
        401; пароль уже задан → 409 password_already_set. Успех → обычный access-token
        (сразу залогинен).
        """
        self._validate_password(password)

        try:
            user_id = uuid.UUID(uid)
        except ValueError as exc:
            raise unauthorized() from exc

        user = await self._users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise unauthorized()
        if user.password_hash is not None:
            # Пароль уже задан (гонка/повтор) — беспарольная ветка неприменима.
            raise password_already_set()

        user.password_hash = hash_password(password)
        # Установка пароля «первого входа» = первый вход беспарольного (ADR-025):
        # проставляем метку первого входа идемпотентно (ADR-028).
        if user.first_login_at is None:
            user.first_login_at = datetime.now(UTC)
        await self._users.session.commit()

        token, expires_in = issue_access_token(
            sub=user.username,
            role=user.role.name,
            superadmin=False,
            uid=str(user.id),
        )
        logger.info("password_set", user_id=str(user.id))
        return LoginResponse(
            password_setup_required=False,
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
        )

    async def _resolve_db_user(self, identifier: str) -> User | None:
        """Ищет пользователя по `username` точно, иначе по нормализованному `telegram`."""
        user = await self._users.get_by_username(identifier)
        if user is not None:
            return user
        return await self._users.get_by_telegram(normalize_telegram(identifier))

    @staticmethod
    def _validate_password(password: str) -> None:
        """Проверяет длину пароля первого входа; нарушение → 422 unprocessable."""
        if not (_PASSWORD_MIN_LEN <= len(password) <= _PASSWORD_MAX_LEN):
            raise unprocessable(
                "Пароль должен быть длиной 8–128 символов",
                details=[{"field": "password", "message": "Недопустимая длина пароля"}],
            )
