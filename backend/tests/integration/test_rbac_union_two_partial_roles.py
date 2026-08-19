"""RBAC-инвариант «две частичные роли» end-to-end на реальном Postgres (ADR-079 §2/§4).

Отличие от `test_auth_me_is_admin_level.py` и `test_users_roles_api.py`: там принципал
**инжектируется** (`dependency_overrides[get_current_principal]`) и БД фейковая, поэтому
союз ролей проверяется только на уровне предиката. Здесь подменяется ТОЛЬКО сессия —
принципал собирает боевой `get_current_principal` из строк `user_roles` реальной БД по
`uid` выданного JWT. Именно эта цепочка (две частичные роли в БД → union прав → admin-
уровень) и является нормой ADR-079 §2; отдельно она нигде не покрыта.

Проверяется, что носитель ДВУХ частичных ролей, union которых == полный каталог:
(а) получает `is_admin_level: true` в `GET /api/auth/me`;
(б) проходит `require_admin` на `GET /api/users` (200);
(в) реально пользуется правом `documents.share` (`PATCH /nodes/{id}/visibility` → 200),
а носитель ОДНОЙ из тех же ролей (union неполный) получает `403` на `/api/users`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.domain.permissions import CATALOG, full_catalog_permissions
from app.models.role import Role
from app.models.user import User
from app.models.user_role import user_roles
from documents_helpers import client, documents_db, seed_node
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_LEFT_ROLE = "Кадры"
_RIGHT_ROLE = "Техподдержка"


def _split_catalog() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Каталог, разложенный на две ЧАСТИЧНЫЕ роли (union == полный каталог).

    Страницы раздаются через одну, а «documents» расщепляется по действиям: `share`
    достаётся только первой роли. Ни одна роль в одиночку не является полным каталогом.
    """
    left: dict[str, list[str]] = {}
    right: dict[str, list[str]] = {}
    for index, (page, actions) in enumerate(CATALOG.items()):
        if page == "documents":
            left[page] = ["view", "share"]
            right[page] = [a for a in actions if a != "share"]
            continue
        (left if index % 2 == 0 else right)[page] = list(actions)
    return left, right


def _app(sm: async_sessionmaker[AsyncSession]) -> Any:
    """Приложение с подменой ТОЛЬКО сессии: принципал собирается боевым кодом из БД."""
    from app.api import deps
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sm() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[deps.get_session] = _session
    return app


def _auth_header(username: str, user_id: uuid.UUID) -> dict[str, str]:
    """Боевой access-token БД-пользователя (`uid` в claim'ах, `role` — информационный)."""
    from app.infra.jwt import issue_access_token

    token, _ = issue_access_token(sub=username, role=_LEFT_ROLE, superadmin=False, uid=str(user_id))
    return {"Authorization": f"Bearer {token}"}


async def _seed_user_with_roles(
    session: AsyncSession, username: str, permissions_per_role: list[dict[str, list[str]]]
) -> User:
    """Пользователь + по строке `user_roles` на каждую переданную роль (ADR-079 §1)."""
    user = User(username=username, password_hash="x", is_active=True)
    session.add(user)
    await session.flush()
    for index, permissions in enumerate(permissions_per_role):
        role = Role(name=f"{username}-роль-{index}", permissions=permissions)
        session.add(role)
        await session.flush()
        await session.execute(insert(user_roles).values(user_id=user.id, role_id=role.id))
    return user


async def test_two_partial_roles_union_grants_admin_level_end_to_end() -> None:
    """Две частичные роли из БД → `is_admin_level` в `/me`, 200 на `/api/users`, share работает."""
    left, right = _split_catalog()
    async with documents_db() as sm:
        async with sm() as s:
            user = User(username="союзник", password_hash="x", is_active=True)
            s.add(user)
            await s.flush()
            role_left = Role(name=_LEFT_ROLE, permissions=left)
            role_right = Role(name=_RIGHT_ROLE, permissions=right)
            s.add_all([role_left, role_right])
            await s.flush()
            await s.execute(insert(user_roles).values(user_id=user.id, role_id=role_left.id))
            await s.execute(insert(user_roles).values(user_id=user.id, role_id=role_right.id))
            node = await seed_node(s, node_type="document", name="Док", visibility_mode="inherit")
            await s.commit()
            headers = _auth_header(user.username, user.id)
            node_id = str(node.id)
            target_role_id = str(role_right.id)

        app = _app(sm)
        async with client(app) as c:
            me = await c.get("/api/auth/me", headers=headers)
            users = await c.get("/api/users", headers=headers)
            shared = await c.patch(
                f"/api/documents/nodes/{node_id}/visibility",
                json={"visibility_mode": "restricted", "role_ids": [target_role_id]},
                headers=headers,
            )
            visibility = await c.get(f"/api/documents/nodes/{node_id}/visibility", headers=headers)

    # (а) /me: предикат посчитан по UNION прав двух ролей, набор ролей отдан массивом.
    assert me.status_code == 200
    body = me.json()
    assert body["is_admin_level"] is True
    assert body["is_superadmin"] is False
    assert sorted(body["roles"]) == sorted([_LEFT_ROLE, _RIGHT_ROLE])
    assert body["permissions"] == full_catalog_permissions()

    # (б) require_admin пропускает носителя union'а (ни одна роль в одиночку не полная).
    assert users.status_code == 200

    # (в) право `documents.share` действительно работает — смена видимости узла по ролям.
    assert shared.status_code == 200
    assert visibility.status_code == 200
    assert visibility.json()["visibility_mode"] == "restricted"
    assert visibility.json()["role_ids"] == [target_role_id]


async def test_single_partial_role_without_full_union_is_403_on_users() -> None:
    """Носитель ОДНОЙ из тех же ролей (union неполный) → `403` на `/api/users`, `/me` — false."""
    left, _right = _split_catalog()
    async with documents_db() as sm:
        async with sm() as s:
            user = await _seed_user_with_roles(s, "одиночка", [left])
            await s.commit()
            headers = _auth_header(user.username, user.id)

        app = _app(sm)
        async with client(app) as c:
            me = await c.get("/api/auth/me", headers=headers)
            users = await c.get("/api/users", headers=headers)

    assert me.status_code == 200
    assert me.json()["is_admin_level"] is False
    assert users.status_code == 403
    assert users.json()["error"]["code"] == "forbidden"
