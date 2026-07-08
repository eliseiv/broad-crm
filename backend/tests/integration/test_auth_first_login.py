"""Тесты идемпотентной установки `users.first_login_at` в auth-потоке (ADR-028).

Метка ПЕРВОГО успешного входа проставляется идемпотентно (`if first_login_at is None`):
  - парольная ветка `login` — при первом успешном bcrypt-логине; повторные входы НЕ меняют;
  - `set_password` — «первый вход» беспарольного (после set-password сразу залогинен);
  - беспарольная ветка `login` (только setup-token) — метку НЕ ставит (вход не выполнен).
Реальный `AuthService` поверх in-memory фейков (conftest.RbacFakeDb).
"""

from __future__ import annotations

import pytest
from app.config import get_settings
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
async def test_password_login_sets_first_login_at_once() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Никита", role, password_hash=hash_password("s3cret-pass"))
    assert user.first_login_at is None  # ещё не входил

    await _service(db).login(username="Никита", password="s3cret-pass", client_ip="10.0.0.10")

    assert user.first_login_at is not None  # первый вход зафиксирован


@pytest.mark.asyncio
async def test_password_login_is_idempotent_on_repeat() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Никита", role, password_hash=hash_password("s3cret-pass"))
    service = _service(db)

    await service.login(username="Никита", password="s3cret-pass", client_ip="10.0.0.11")
    first = user.first_login_at
    assert first is not None

    await service.login(username="Никита", password="s3cret-pass", client_ip="10.0.0.11")

    # Повторный вход НЕ переписывает метку (семантика «когда впервые вошёл»).
    assert user.first_login_at == first


@pytest.mark.asyncio
async def test_passwordless_login_branch_does_not_set_first_login_at() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Беспарольный", role, password_hash=None)

    resp = await _service(db).login(username="Беспарольный", password=None, client_ip="10.0.0.12")

    # Беспарольная ветка выдаёт только setup-token — вход не выполнен, метки нет.
    assert resp.password_setup_required is True
    assert user.first_login_at is None


@pytest.mark.asyncio
async def test_set_password_sets_first_login_at() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Беспарольный", role, password_hash=None)
    assert user.first_login_at is None

    await _service(db).set_password(uid=str(user.id), password="brand-new-pass")

    # set-password = первый вход беспарольного → метка проставлена.
    assert user.first_login_at is not None


@pytest.mark.asyncio
async def test_set_password_does_not_overwrite_existing_first_login_at() -> None:
    from datetime import UTC, datetime

    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    marked = datetime(2026, 1, 1, tzinfo=UTC)
    user = db.add_user("Беспарольный", role, password_hash=None, first_login_at=marked)

    await _service(db).set_password(uid=str(user.id), password="brand-new-pass")

    # Метка уже была → идемпотентность: не переписывается.
    assert user.first_login_at == marked
