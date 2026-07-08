"""Контрактные тесты Users/Roles/Permissions API (04-api.md, ADR-021).

Реальные роутеры + сервисы поверх in-memory фейков репозиториев (conftest.RbacFakeDb),
`get_current_principal` замокан общим хелпером. Проверяются коды/прецеденция ошибок,
гейт `require_admin` (403 для не-админа), отсутствие пароля в ответах, каталог прав.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.api import deps
from app.services.role_service import RoleService
from app.services.team_service import TeamService
from app.services.user_service import UserService
from conftest import RbacFakeDb, make_principal
from httpx import ASGITransport, AsyncClient


def _build_app(db: RbacFakeDb, principal: Any) -> Any:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    app.dependency_overrides[deps.get_current_principal] = lambda: principal
    app.dependency_overrides[deps.get_user_service] = lambda: UserService(
        users=db.user_repo, roles=db.role_repo, teams=db.team_repo
    )
    app.dependency_overrides[deps.get_role_service] = lambda: RoleService(repository=db.role_repo)
    app.dependency_overrides[deps.get_team_service] = lambda: TeamService(
        teams=db.team_repo, users=db.user_repo
    )
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_permissions_catalog_contract_order_and_no_users_page() -> None:
    app = _build_app(RbacFakeDb(), make_principal())  # супер-админ

    async with _client(app) as client:
        resp = await client.get("/api/permissions/catalog")

    assert resp.status_code == 200
    pages = resp.json()["pages"]
    # Спринт A (ADR-022): каталог включает roles/teams, порядок = строки UI-матрицы.
    assert [p["page"] for p in pages] == [
        "dashboard",
        "servers",
        "ai-keys",
        "proxies",
        "backends",
        "mail",
        "roles",
        "teams",
    ]
    by_page = {p["page"]: p["actions"] for p in pages}
    assert by_page["dashboard"] == ["view"]
    assert by_page["mail"] == ["view"]
    assert by_page["servers"] == ["view", "create", "edit", "delete"]
    assert by_page["roles"] == ["view", "create", "edit", "delete"]
    assert by_page["teams"] == ["view", "create", "edit", "delete"]
    assert "users" not in by_page


@pytest.mark.asyncio
async def test_require_admin_forbids_non_admin_on_all_admin_endpoints() -> None:
    operator = make_principal(
        is_superadmin=False, role="Оператор", permissions={"servers": ["view"]}
    )
    app = _build_app(RbacFakeDb(), operator)

    async with _client(app) as client:
        users = await client.get("/api/users")
        roles = await client.get("/api/roles")
        catalog = await client.get("/api/permissions/catalog")

    for resp in (users, roles, catalog):
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_db_admin_role_passes_require_admin() -> None:
    # Не супер-админ, но role=="admin" → require_admin пропускает (ADR-021 §5).
    admin_role = make_principal(is_superadmin=False, role="admin", permissions={})
    app = _build_app(RbacFakeDb(), admin_role)

    async with _client(app) as client:
        resp = await client.get("/api/users")

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_users_crud_contract_and_password_never_returned() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        created = await client.post(
            "/api/users",
            json={"username": "Никита", "password": "s3cret-pass", "role_id": str(role.id)},
        )
        listed = await client.get("/api/users")
        user_id = created.json()["id"]
        patched = await client.patch(f"/api/users/{user_id}", json={"is_active": False})
        deleted = await client.delete(f"/api/users/{user_id}")
        repeat = await client.delete(f"/api/users/{user_id}")

    assert created.status_code == 201
    assert created.json()["username"] == "Никита"
    assert created.json()["role_name"] == "Оператор"
    assert "password" not in created.text
    assert "s3cret-pass" not in created.text
    assert "password_hash" not in created.text
    assert listed.status_code == 200
    assert [u["username"] for u in listed.json()["items"]] == ["Никита"]
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False
    assert deleted.status_code == 204
    assert repeat.status_code == 404
    assert repeat.json()["error"]["code"] == "user_not_found"


@pytest.mark.asyncio
async def test_users_error_codes_and_precedence() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        bad_username = await client.post(
            "/api/users",
            json={"username": "123", "password": "s3cret-pass", "role_id": str(role.id)},
        )
        missing_role = await client.post(
            "/api/users",
            json={
                "username": "Пётр",
                "password": "s3cret-pass",
                "role_id": "00000000-0000-0000-0000-0000000000ff",
            },
        )
        await client.post(
            "/api/users",
            json={"username": "Никита", "password": "s3cret-pass", "role_id": str(role.id)},
        )
        dup = await client.post(
            "/api/users",
            json={"username": "Никита", "password": "other-pass", "role_id": str(role.id)},
        )
        # PATCH password '' → 422 unprocessable (не «очистка»).
        list_resp = await client.get("/api/users")
        uid = next(u["id"] for u in list_resp.json()["items"] if u["username"] == "Никита")
        empty_pw = await client.patch(f"/api/users/{uid}", json={"password": ""})

    assert bad_username.status_code == 422
    assert bad_username.json()["error"]["code"] == "unprocessable"
    assert bad_username.json()["error"]["details"][0]["field"] == "username"
    assert missing_role.status_code == 422
    assert missing_role.json()["error"]["details"][0]["field"] == "role_id"
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "username_taken"
    assert empty_pw.status_code == 422
    assert empty_pw.json()["error"]["details"][0]["field"] == "password"


@pytest.mark.asyncio
async def test_roles_crud_and_error_codes() -> None:
    db = RbacFakeDb()
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        created = await client.post(
            "/api/roles",
            json={"name": "Оператор", "permissions": {"servers": ["view"], "mail": ["view"]}},
        )
        out_of_catalog = await client.post(
            "/api/roles",
            json={"name": "Плохая", "permissions": {"servers": ["fly"]}},
        )
        dup = await client.post(
            "/api/roles",
            json={"name": "Оператор", "permissions": {}},
        )
        listed = await client.get("/api/roles")

    assert created.status_code == 201
    assert created.json()["name"] == "Оператор"
    assert created.json()["permissions"] == {"servers": ["view"], "mail": ["view"]}
    assert out_of_catalog.status_code == 422
    assert out_of_catalog.json()["error"]["details"][0]["field"] == "permissions"
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "role_name_taken"
    assert "Оператор" in [r["name"] for r in listed.json()["items"]]


@pytest.mark.asyncio
async def test_role_in_use_delete_is_409() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Никита", role)  # роль назначена носителю
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        resp = await client.delete(f"/api/roles/{role.id}")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "role_in_use"


@pytest.mark.asyncio
async def test_role_name_invalid_is_422() -> None:
    app = _build_app(RbacFakeDb(), make_principal())

    async with _client(app) as client:
        resp = await client.post("/api/roles", json={"name": "123", "permissions": {}})

    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "name"


# --- Roles: security-инвариант эскалации через API (роутер прокидывает актора, ADR-022) ---


@pytest.mark.asyncio
async def test_roles_gate_view_forbids_without_roles_view() -> None:
    # Актор без roles:view (только servers:view) → GET /api/roles → 403 (матрица roles:*).
    operator = make_principal(
        is_superadmin=False, role="Оператор", permissions={"servers": ["view"]}
    )
    app = _build_app(RbacFakeDb(), operator)

    async with _client(app) as client:
        resp = await client.get("/api/roles")

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_roles_create_escalation_forbidden_via_api() -> None:
    # Не-админ с roles:create, но правами только servers:view — не может выдать servers:delete.
    operator = make_principal(
        is_superadmin=False,
        role="Оператор",
        permissions={"roles": ["view", "create"], "servers": ["view"]},
    )
    app = _build_app(RbacFakeDb(), operator)

    async with _client(app) as client:
        escalate = await client.post(
            "/api/roles",
            json={"name": "Мощный", "permissions": {"servers": ["view", "delete"]}},
        )
        subset = await client.post(
            "/api/roles",
            json={"name": "Скромный", "permissions": {"servers": ["view"]}},
        )

    assert escalate.status_code == 403
    assert escalate.json()["error"]["code"] == "forbidden"
    assert subset.status_code == 201
    assert subset.json()["user_count"] == 0


@pytest.mark.asyncio
async def test_roles_edit_admin_by_non_admin_forbidden_via_api() -> None:
    # Не-админ с roles:edit пытается изменить встроенную роль admin → 403 (защита admin).
    db = RbacFakeDb()
    admin_role = db.add_role("admin", {"servers": ["view"]})
    operator = make_principal(
        is_superadmin=False, role="Оператор", permissions={"roles": ["view", "edit"]}
    )
    app = _build_app(db, operator)

    async with _client(app) as client:
        resp = await client.patch(f"/api/roles/{admin_role.id}", json={"name": "Администраторы"})

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_roles_delete_admin_by_non_admin_forbidden_via_api() -> None:
    db = RbacFakeDb()
    admin_role = db.add_role("admin", {"servers": ["view"]})
    operator = make_principal(
        is_superadmin=False, role="Оператор", permissions={"roles": ["view", "delete"]}
    )
    app = _build_app(db, operator)

    async with _client(app) as client:
        resp = await client.delete(f"/api/roles/{admin_role.id}")

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert admin_role.id in db.roles


@pytest.mark.asyncio
async def test_roles_admin_can_edit_admin_role_via_api() -> None:
    # Привилегированный актор (role==admin) проходит защиту admin.
    db = RbacFakeDb()
    admin_role = db.add_role("admin", {"servers": ["view"]})
    app = _build_app(db, make_principal(is_superadmin=False, role="admin"))

    async with _client(app) as client:
        resp = await client.patch(
            f"/api/roles/{admin_role.id}", json={"permissions": {"servers": ["view", "edit"]}}
        )

    assert resp.status_code == 200
    assert resp.json()["permissions"] == {"servers": ["view", "edit"]}


@pytest.mark.asyncio
async def test_roles_list_and_patch_report_user_count() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Никита", role)
    db.add_user("Мария", role)
    app = _build_app(db, make_principal())  # супер-админ

    async with _client(app) as client:
        listed = await client.get("/api/roles")
        patched = await client.patch(f"/api/roles/{role.id}", json={"name": "Операторы"})

    counts = {r["name"]: r["user_count"] for r in listed.json()["items"]}
    assert counts["Оператор"] == 2
    assert patched.status_code == 200
    assert patched.json()["user_count"] == 2


# --- Users: email/teams через API (ADR-022) ---


@pytest.mark.asyncio
async def test_users_email_and_teams_contract_via_api() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    leader = db.add_user("Лидер", role)
    team = db.add_team("Продажи", leader)
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        created = await client.post(
            "/api/users",
            json={
                "username": "Никита",
                "email": "Nikita@Example.com",
                "password": "s3cret-pass",
                "role_id": str(role.id),
                "team_ids": [str(team.id)],
            },
        )
        # Дубликат email → 409 email_taken.
        dup_email = await client.post(
            "/api/users",
            json={
                "username": "Пётр",
                "email": "nikita@example.com",
                "password": "s3cret-pass",
                "role_id": str(role.id),
            },
        )

    assert created.status_code == 201
    body = created.json()
    assert body["email"] == "nikita@example.com"  # нормализован
    assert [t["name"] for t in body["teams"]] == ["Продажи"]
    assert dup_email.status_code == 409
    assert dup_email.json()["error"]["code"] == "email_taken"


@pytest.mark.asyncio
async def test_users_invalid_email_is_422_via_api() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        resp = await client.post(
            "/api/users",
            json={
                "username": "Никита",
                "email": "bad-email",
                "password": "s3cret-pass",
                "role_id": str(role.id),
            },
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "email"


@pytest.mark.asyncio
async def test_users_nonexistent_team_id_is_422_via_api() -> None:
    db = RbacFakeDb()
    role = db.add_role("Оператор", {"servers": ["view"]})
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        resp = await client.post(
            "/api/users",
            json={
                "username": "Никита",
                "password": "s3cret-pass",
                "role_id": str(role.id),
                "team_ids": ["00000000-0000-0000-0000-0000000000aa"],
            },
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "team_ids"
