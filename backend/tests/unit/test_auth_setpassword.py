"""Тесты «открытого первого входа» ADR-025: вход по Телеграму, беспарольная ветка,
set_password, limited-scope setup-token.

Реальный `AuthService` поверх in-memory фейков (conftest.RbacFakeDb). Проверяют:
идентификатор = Логин ИЛИ нормализованный Телеграм; беспарольный пользователь →
`password_setup_required=true` + setup-token; `set_password` (успех/409/422/401);
setup-token отвергается там, где ждут access, и наоборот (jwt-слой).
"""

from __future__ import annotations

import uuid

import pytest
from app.config import get_settings
from app.errors import AppError
from app.infra.jwt import (
    TokenError,
    decode_access_token,
    decode_setup_token,
    issue_access_token,
    issue_setup_token,
)
from app.infra.passwords import hash_password, verify_password
from app.infra.rate_limit import InMemoryRateLimiter
from app.services.auth_service import AuthService
from conftest import RbacFakeDb


def _service(db: RbacFakeDb) -> AuthService:
    return AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=100, window_sec=300),
        user_repository=db.user_repo,
    )


# --------------------------------------------------------- вход по Логину/Телеграму
@pytest.mark.asyncio
async def test_login_by_normalized_telegram_identifier() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    # Хранится нормализованным (без @, lower-case).
    user = db.add_user(
        "Никита", role, password_hash=hash_password("s3cret-pass"), telegram="nick_01"
    )

    # Вход по @Nick_01 / NICK_01 → нормализуется в nick_01 → успех.
    for identifier in ("@Nick_01", "NICK_01", "nick_01"):
        resp = await _service(db).login(
            username=identifier, password="s3cret-pass", client_ip="10.0.0.1"
        )
        assert resp.password_setup_required is False
        assert decode_access_token(resp.access_token).uid == str(user.id)


@pytest.mark.asyncio
async def test_login_username_takes_precedence_over_telegram() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("nick_01", role, password_hash=hash_password("by-username"))
    db.add_user("Другой", role, password_hash=hash_password("by-telegram"), telegram="nick_01")

    # Точный матч по username выигрывает у матча по telegram.
    resp = await _service(db).login(
        username="nick_01", password="by-username", client_ip="10.0.0.2"
    )
    assert decode_access_token(resp.access_token).sub == "nick_01"


# ----------------------------------------------------- беспарольный → setup-token
@pytest.mark.asyncio
async def test_passwordless_login_returns_setup_token_not_access() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Беспарольный", role, password_hash=None)

    resp = await _service(db).login(username="Беспарольный", password=None, client_ip="10.0.0.3")

    assert resp.password_setup_required is True
    assert resp.access_token is None
    assert resp.setup_token is not None
    # Setup-token — limited-scope: type pwd_setup, несёт uid, но НЕ access.
    claims = decode_setup_token(resp.setup_token)
    assert claims.uid == str(user.id)
    with pytest.raises(TokenError):
        decode_access_token(resp.setup_token)


@pytest.mark.asyncio
async def test_passwordless_login_ignores_supplied_password() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Беспарольный", role, password_hash=None)

    # Даже с непустым паролем беспарольный получает setup-flow (пароль ещё не задан).
    resp = await _service(db).login(
        username="Беспарольный", password="anything", client_ip="10.0.0.4"
    )
    assert resp.password_setup_required is True


# ---------------------------------------------------------------- set_password
@pytest.mark.asyncio
async def test_set_password_success_issues_access_token() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Беспарольный", role, password_hash=None)

    resp = await _service(db).set_password(uid=str(user.id), password="brand-new-pass")

    assert resp.password_setup_required is False
    assert resp.access_token is not None
    assert decode_access_token(resp.access_token).uid == str(user.id)
    # Хэш установлен → вход теперь только по паролю.
    assert user.password_hash is not None
    assert verify_password("brand-new-pass", user.password_hash) is True


@pytest.mark.asyncio
async def test_set_password_already_set_is_409() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Парольный", role, password_hash=hash_password("existing-pass"))

    with pytest.raises(AppError) as exc:
        await _service(db).set_password(uid=str(user.id), password="new-strong-pass")
    assert exc.value.status_code == 409
    assert exc.value.code == "password_already_set"


@pytest.mark.asyncio
async def test_set_password_weak_is_422() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Беспарольный", role, password_hash=None)

    with pytest.raises(AppError) as exc:
        await _service(db).set_password(uid=str(user.id), password="short")
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "password"
    assert user.password_hash is None  # пароль не установлен


@pytest.mark.asyncio
async def test_set_password_unknown_uid_is_401() -> None:
    db = RbacFakeDb()

    with pytest.raises(AppError) as exc:
        await _service(db).set_password(uid=str(uuid.uuid4()), password="brand-new-pass")
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthorized"


@pytest.mark.asyncio
async def test_set_password_inactive_user_is_401() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Выключенный", role, password_hash=None, is_active=False)

    with pytest.raises(AppError) as exc:
        await _service(db).set_password(uid=str(user.id), password="brand-new-pass")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_set_password_malformed_uid_is_401() -> None:
    db = RbacFakeDb()

    with pytest.raises(AppError) as exc:
        await _service(db).set_password(uid="not-a-uuid", password="brand-new-pass")
    assert exc.value.status_code == 401


# ----------------------------------------------- setup-token: границы типа (jwt-слой)
def test_setup_token_roundtrip_and_ttl() -> None:
    token, expires_in = issue_setup_token(sub="Никита", uid="u-1")
    # TTL по умолчанию — PWD_SETUP_TOKEN_EXPIRES_MIN (10 мин).
    assert expires_in == get_settings().pwd_setup_token_expires_min * 60
    claims = decode_setup_token(token)
    assert claims.sub == "Никита"
    assert claims.uid == "u-1"


def test_access_token_rejected_by_setup_decoder() -> None:
    # Обычный access-token НЕ принимается set-password-декодером (разные type).
    token, _ = issue_access_token(sub="admin", role="admin", superadmin=True, uid="u-2")
    with pytest.raises(TokenError):
        decode_setup_token(token)


def test_setup_token_rejected_by_access_decoder() -> None:
    # Setup-token НЕ даёт доступа к ресурсам (get_current_principal → decode_access_token).
    token, _ = issue_setup_token(sub="Никита", uid="u-3")
    with pytest.raises(TokenError):
        decode_access_token(token)
