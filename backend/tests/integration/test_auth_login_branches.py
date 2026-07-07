"""Тесты обеих веток логина (ADR-021 §4, 05-security.md, app/services/auth_service.py).

Порядок: СПЕРВА `.env`-супер-админ (constant-time plaintext, `superadmin=true`, без `uid`),
ЗАТЕМ БД-пользователь (bcrypt + `is_active`, `superadmin=false`, с `uid`). Неудача любой
ветки → единое 401 invalid_credentials (user-enumeration не раскрывается).
"""

from __future__ import annotations

import pytest
from app.config import get_settings
from app.errors import AppError
from app.infra.jwt import decode_access_token
from app.infra.passwords import hash_password
from app.infra.rate_limit import InMemoryRateLimiter
from app.services.auth_service import AuthService
from conftest import RbacFakeDb


def _service(db: RbacFakeDb) -> AuthService:
    return AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=100, window_sec=300),
        user_repository=db.user_repo,
    )


@pytest.mark.asyncio
async def test_superadmin_branch_issues_superadmin_token_without_uid() -> None:
    token = await _service(RbacFakeDb()).login(
        username="admin", password="secret", client_ip="10.0.0.1"
    )

    claims = decode_access_token(token.access_token)
    assert claims.sub == "admin"
    assert claims.role == "admin"
    assert claims.superadmin is True
    assert claims.uid is None


@pytest.mark.asyncio
async def test_db_user_branch_issues_token_with_uid_and_role() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Никита", role, password_hash=hash_password("s3cret-pass"))

    token = await _service(db).login(
        username="Никита", password="s3cret-pass", client_ip="10.0.0.2"
    )

    claims = decode_access_token(token.access_token)
    assert claims.sub == "Никита"
    assert claims.role == "Оператор"
    assert claims.superadmin is False
    assert claims.uid == str(user.id)


@pytest.mark.asyncio
async def test_inactive_db_user_is_401_invalid_credentials() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Пётр", role, password_hash=hash_password("s3cret-pass"), is_active=False)

    with pytest.raises(AppError) as exc:
        await _service(db).login(username="Пётр", password="s3cret-pass", client_ip="10.0.0.3")
    assert exc.value.status_code == 401
    assert exc.value.code == "invalid_credentials"


@pytest.mark.asyncio
async def test_wrong_password_and_unknown_user_are_401() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Никита", role, password_hash=hash_password("s3cret-pass"))
    service = _service(db)

    with pytest.raises(AppError) as wrong:
        await service.login(username="Никита", password="nope", client_ip="10.0.0.4")
    with pytest.raises(AppError) as unknown:
        await service.login(username="Призрак", password="whatever", client_ip="10.0.0.5")

    assert wrong.value.code == "invalid_credentials"
    assert unknown.value.code == "invalid_credentials"
