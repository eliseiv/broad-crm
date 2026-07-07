"""Тесты RBAC-claim'ов JWT (ADR-021, 05-security.md, app/infra/jwt.py).

Супер-админ — без `uid`; БД-пользователь — с `uid`. Легаси-токен (без role/superadmin)
→ TokenError (повторный вход). Алгоритм HS256, TTL из JWT_EXPIRES_MIN.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.config import get_settings
from app.infra.jwt import AccessTokenClaims, TokenError, decode_access_token, issue_access_token


def test_superadmin_token_has_no_uid() -> None:
    token, expires_in = issue_access_token(sub="admin", role="admin", superadmin=True)

    assert expires_in == 86400  # JWT_EXPIRES_MIN=1440 → 86400 сек
    claims = decode_access_token(token)
    assert claims == AccessTokenClaims(sub="admin", role="admin", superadmin=True, uid=None)
    # `uid` физически отсутствует в payload супер-админа.
    payload = jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=[get_settings().jwt_algorithm],
    )
    assert "uid" not in payload


def test_db_user_token_carries_uid_and_role() -> None:
    token, _ = issue_access_token(
        sub="Никита", role="Оператор", superadmin=False, uid="2a9f0000-0000-0000-0000-0000000000c0"
    )

    claims = decode_access_token(token)
    assert claims.sub == "Никита"
    assert claims.role == "Оператор"
    assert claims.superadmin is False
    assert claims.uid == "2a9f0000-0000-0000-0000-0000000000c0"


def test_legacy_token_without_rbac_claims_is_rejected() -> None:
    # Легаси-токен до Спринта 3: только sub/type/iat/exp, без role/superadmin.
    settings = get_settings()
    now = datetime.now(UTC)
    legacy = jwt.encode(
        {
            "sub": "admin",
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError):
        decode_access_token(legacy)


def test_token_missing_superadmin_claim_is_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    partial = jwt.encode(
        {
            "sub": "admin",
            "role": "admin",  # role есть, superadmin нет → всё ещё легаси
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError):
        decode_access_token(partial)


def test_wrong_type_and_empty_and_expired_tokens_are_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)

    with pytest.raises(TokenError):
        decode_access_token("")

    wrong_type = jwt.encode(
        {
            "sub": "admin",
            "role": "admin",
            "superadmin": True,
            "type": "refresh",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(TokenError):
        decode_access_token(wrong_type)

    expired = jwt.encode(
        {
            "sub": "admin",
            "role": "admin",
            "superadmin": True,
            "type": "access",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(TokenError):
        decode_access_token(expired)


def test_bad_uid_type_in_token_is_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    bad_uid = jwt.encode(
        {
            "sub": "Никита",
            "role": "Оператор",
            "superadmin": False,
            "uid": 12345,  # не строка
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=1)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(TokenError):
        decode_access_token(bad_uid)
