"""Тесты серверного enforcement RBAC (ADR-021 §5, app/api/deps.py).

`get_current_principal` — свежая загрузка прав из БД на каждый запрос: правки роли
применяются без пере-логина; деактивация/удаление пользователя аннулируют JWT (401).
Фабрики `require(page, action)` и `require_admin` — 403 при отсутствии права.
Сервер — единственная граница безопасности; UI-гейтинг лишь UX.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from app.api.deps import Principal, get_current_principal, require, require_admin
from app.errors import AppError
from app.infra.jwt import issue_access_token
from fastapi.security import HTTPAuthorizationCredentials


class _FakeSession:
    """Фейк AsyncSession: `get(User, pk)` возвращает засеянного пользователя или None."""

    def __init__(self, user: Any | None) -> None:
        self._user = user
        self.get_calls = 0

    async def get(self, _model: Any, pk: uuid.UUID) -> Any | None:
        self.get_calls += 1
        if self._user is not None and self._user.id == pk:
            return self._user
        return None


def _creds(token: str | None) -> HTTPAuthorizationCredentials | None:
    if token is None:
        return None
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _db_user(*, is_active: bool = True, permissions: dict[str, list[str]] | None = None) -> Any:
    role = SimpleNamespace(
        name="Оператор",
        permissions={"servers": ["view"], "mail": ["view"]} if permissions is None else permissions,
    )
    return SimpleNamespace(id=uuid.uuid4(), username="Никита", role=role, is_active=is_active)


@pytest.mark.asyncio
async def test_superadmin_token_yields_full_catalog_without_db_hit() -> None:
    session = _FakeSession(None)
    token, _ = issue_access_token(sub="admin", role="admin", superadmin=True)

    principal = await get_current_principal(session, _creds(token))  # type: ignore[arg-type]

    assert principal.is_superadmin is True
    assert principal.role == "admin"
    assert principal.permissions["servers"] == ["view", "create", "edit", "delete"]
    assert session.get_calls == 0  # супер-админ не читает БД


@pytest.mark.asyncio
async def test_db_user_token_loads_permissions_from_db() -> None:
    user = _db_user()
    session = _FakeSession(user)
    token, _ = issue_access_token(
        sub=user.username, role=user.role.name, superadmin=False, uid=str(user.id)
    )

    principal = await get_current_principal(session, _creds(token))  # type: ignore[arg-type]

    assert principal.is_superadmin is False
    assert principal.username == "Никита"
    assert principal.role == "Оператор"
    assert principal.permissions == {"servers": ["view"], "mail": ["view"]}
    assert session.get_calls == 1


@pytest.mark.asyncio
async def test_role_permission_change_applies_without_relogin() -> None:
    user = _db_user(permissions={"servers": ["view"]})
    session = _FakeSession(user)
    token, _ = issue_access_token(
        sub=user.username, role=user.role.name, superadmin=False, uid=str(user.id)
    )

    first = await get_current_principal(session, _creds(token))  # type: ignore[arg-type]
    assert first.permissions == {"servers": ["view"]}

    # Админ расширил права роли — тем же токеном следующий запрос видит новые права.
    user.role.permissions = {"servers": ["view", "edit"], "ai-keys": ["view"]}
    second = await get_current_principal(session, _creds(token))  # type: ignore[arg-type]
    assert second.permissions == {"servers": ["view", "edit"], "ai-keys": ["view"]}


@pytest.mark.asyncio
async def test_missing_credentials_is_401() -> None:
    with pytest.raises(AppError) as exc:
        await get_current_principal(_FakeSession(None), _creds(None))  # type: ignore[arg-type]
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthorized"


@pytest.mark.asyncio
async def test_invalid_token_is_401() -> None:
    with pytest.raises(AppError) as exc:
        await get_current_principal(_FakeSession(None), _creds("garbage"))  # type: ignore[arg-type]
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_db_user_not_found_annuls_session_401() -> None:
    # Токен валиден, но пользователь удалён из БД → 401 без пере-логина.
    ghost_id = uuid.uuid4()
    session = _FakeSession(None)
    token, _ = issue_access_token(
        sub="Никита", role="Оператор", superadmin=False, uid=str(ghost_id)
    )

    with pytest.raises(AppError) as exc:
        await get_current_principal(session, _creds(token))  # type: ignore[arg-type]
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_deactivated_user_is_401() -> None:
    user = _db_user(is_active=False)
    session = _FakeSession(user)
    token, _ = issue_access_token(
        sub=user.username, role=user.role.name, superadmin=False, uid=str(user.id)
    )

    with pytest.raises(AppError) as exc:
        await get_current_principal(session, _creds(token))  # type: ignore[arg-type]
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_non_superadmin_token_without_uid_is_401() -> None:
    token, _ = issue_access_token(sub="Никита", role="Оператор", superadmin=False)

    with pytest.raises(AppError) as exc:
        await get_current_principal(_FakeSession(None), _creds(token))  # type: ignore[arg-type]
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_uuid_uid_is_401() -> None:
    token, _ = issue_access_token(sub="Никита", role="Оператор", superadmin=False, uid="not-a-uuid")

    with pytest.raises(AppError) as exc:
        await get_current_principal(_FakeSession(None), _creds(token))  # type: ignore[arg-type]
    assert exc.value.status_code == 401


# --- require(page, action) и require_admin ---


def _principal(*, is_superadmin: bool, role: str, permissions: dict[str, list[str]]) -> Principal:
    return Principal(username="u", role=role, permissions=permissions, is_superadmin=is_superadmin)


@pytest.mark.asyncio
async def test_require_allows_superadmin_and_permitted_action() -> None:
    superadmin = _principal(is_superadmin=True, role="admin", permissions={})
    operator = _principal(is_superadmin=False, role="Оператор", permissions={"servers": ["view"]})

    assert await require("servers", "view")(superadmin) is superadmin
    assert await require("servers", "view")(operator) is operator


@pytest.mark.asyncio
async def test_require_forbids_missing_action_403() -> None:
    operator = _principal(is_superadmin=False, role="Оператор", permissions={"servers": ["view"]})

    with pytest.raises(AppError) as exc:
        await require("servers", "delete")(operator)
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"

    # Страница вовсе отсутствует в правах → тоже 403.
    with pytest.raises(AppError) as exc2:
        await require("ai-keys", "view")(operator)
    assert exc2.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_allows_superadmin_and_admin_role() -> None:
    superadmin = _principal(is_superadmin=True, role="admin", permissions={})
    admin_role = _principal(is_superadmin=False, role="admin", permissions={})

    assert await require_admin(superadmin) is superadmin
    assert await require_admin(admin_role) is admin_role


@pytest.mark.asyncio
async def test_require_admin_forbids_non_admin_403() -> None:
    operator = _principal(is_superadmin=False, role="Оператор", permissions={"servers": ["view"]})

    with pytest.raises(AppError) as exc:
        await require_admin(operator)
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"
