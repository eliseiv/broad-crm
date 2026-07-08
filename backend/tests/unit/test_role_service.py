"""Тесты RoleService: валидация имени/прав, коды 422/409, role_in_use, эскалация (ADR-022).

Реальный сервис поверх in-memory фейков репозиториев (conftest.RbacFakeDb). Прецеденция
(04-api.md): схемная валидация name (422) → permissions против каталога (422) →
subset-инвариант эскалации (403 для не-админа) → уникальность name (409 role_name_taken).
Роль с носителями удалить нельзя → 409 role_in_use. `user_count` — агрегат носителей роли.
"""

from __future__ import annotations

import uuid

import pytest
from app.domain.permissions import full_catalog_permissions
from app.errors import AppError
from app.schemas.role import RoleCreateRequest, RoleUpdateRequest
from app.services.role_service import RoleService
from conftest import RbacFakeDb


@pytest.fixture
def db() -> RbacFakeDb:
    return RbacFakeDb()


def _service(db: RbacFakeDb) -> RoleService:
    return RoleService(repository=db.role_repo)


# Привилегированный актор (супер-админ / роль admin): полный каталог, эскалация не проверяется.
def _priv() -> dict[str, object]:
    return {"actor_permissions": full_catalog_permissions(), "actor_privileged": True}


@pytest.mark.asyncio
async def test_create_role_valid(db: RbacFakeDb) -> None:
    service = _service(db)

    item = await service.create_role(
        RoleCreateRequest(name="Оператор", permissions={"servers": ["view"], "mail": ["view"]}),
        **_priv(),
    )

    assert item.name == "Оператор"
    assert item.permissions == {"servers": ["view"], "mail": ["view"]}
    assert item.user_count == 0


@pytest.mark.asyncio
async def test_create_role_invalid_name_is_422_field_name(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(RoleCreateRequest(name="123", permissions={}), **_priv())
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "name"


@pytest.mark.asyncio
async def test_create_role_permissions_out_of_catalog_is_422_field_permissions(
    db: RbacFakeDb,
) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(
            RoleCreateRequest(name="Оператор", permissions={"servers": ["fly"]}), **_priv()
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "permissions"


@pytest.mark.asyncio
async def test_create_role_rejects_users_page_in_permissions(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(
            RoleCreateRequest(name="Оператор", permissions={"users": ["view"]}), **_priv()
        )
    assert exc.value.details[0]["field"] == "permissions"


@pytest.mark.asyncio
async def test_create_role_precedence_name_before_permissions(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(
            RoleCreateRequest(name="...", permissions={"servers": ["fly"]}), **_priv()
        )
    assert exc.value.details[0]["field"] == "name"


@pytest.mark.asyncio
async def test_create_role_duplicate_name_is_409(db: RbacFakeDb) -> None:
    service = _service(db)
    await service.create_role(RoleCreateRequest(name="Оператор", permissions={}), **_priv())

    with pytest.raises(AppError) as exc:
        await service.create_role(RoleCreateRequest(name="Оператор", permissions={}), **_priv())
    assert exc.value.status_code == 409
    assert exc.value.code == "role_name_taken"


@pytest.mark.asyncio
async def test_create_role_race_integrity_maps_409(db: RbacFakeDb) -> None:
    db.session.raise_integrity = True
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(RoleCreateRequest(name="Гонка", permissions={}), **_priv())
    assert exc.value.code == "role_name_taken"


@pytest.mark.asyncio
async def test_update_role_not_found_is_404(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(uuid.uuid4(), RoleUpdateRequest(name="Новое"), **_priv())
    assert exc.value.status_code == 404
    assert exc.value.code == "role_not_found"


@pytest.mark.asyncio
async def test_update_role_rename_to_taken_is_409(db: RbacFakeDb) -> None:
    db.add_role("Оператор", {})
    target = db.add_role("Гость", {})
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(target.id, RoleUpdateRequest(name="Оператор"), **_priv())
    assert exc.value.status_code == 409
    assert exc.value.code == "role_name_taken"


@pytest.mark.asyncio
async def test_update_role_permissions_out_of_catalog_is_422(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(
            role.id, RoleUpdateRequest(permissions={"backends": ["nope"]}), **_priv()
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "permissions"


@pytest.mark.asyncio
async def test_update_role_applies_name_and_permissions(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    service = _service(db)

    item = await service.update_role(
        role.id,
        RoleUpdateRequest(name="Супероператор", permissions={"servers": ["view", "edit"]}),
        **_priv(),
    )

    assert item.name == "Супероператор"
    assert item.permissions == {"servers": ["view", "edit"]}


@pytest.mark.asyncio
async def test_update_role_returns_user_count(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Никита", role)
    db.add_user("Мария", role)
    service = _service(db)

    item = await service.update_role(role.id, RoleUpdateRequest(name="Операторы"), **_priv())

    assert item.user_count == 2


@pytest.mark.asyncio
async def test_delete_role_in_use_is_409(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Никита", role)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_role(role.id, actor_privileged=True)
    assert exc.value.status_code == 409
    assert exc.value.code == "role_in_use"
    assert role.id in db.roles  # не удалена


@pytest.mark.asyncio
async def test_delete_role_not_found_is_404(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_role(uuid.uuid4(), actor_privileged=True)
    assert exc.value.status_code == 404
    assert exc.value.code == "role_not_found"


@pytest.mark.asyncio
async def test_delete_role_success(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {})
    service = _service(db)

    await service.delete_role(role.id, actor_privileged=True)

    assert role.id not in db.roles


@pytest.mark.asyncio
async def test_delete_role_race_integrity_maps_409(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {})
    db.session.raise_integrity = True
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_role(role.id, actor_privileged=True)
    assert exc.value.code == "role_in_use"


@pytest.mark.asyncio
async def test_list_roles_returns_all_with_user_count(db: RbacFakeDb) -> None:
    admin = db.add_role("admin", {})
    operator = db.add_role("Оператор", {})
    db.add_user("Никита", operator)
    db.add_user("Мария", operator)
    db.add_user("Босс", admin)
    service = _service(db)

    result = await service.list_roles()

    by_name = {r.name: r.user_count for r in result.items}
    assert by_name == {"admin": 1, "Оператор": 2}


# --- Security-инвариант эскалации (ADR-022 §4, критично) ---


@pytest.mark.asyncio
async def test_create_role_non_admin_escalation_is_403(db: RbacFakeDb) -> None:
    # Актор имеет только servers:view; пытается создать роль с servers:delete (сверх своих).
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(
            RoleCreateRequest(name="Оператор", permissions={"servers": ["view", "delete"]}),
            actor_permissions={"servers": ["view"]},
            actor_privileged=False,
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"


@pytest.mark.asyncio
async def test_create_role_non_admin_subset_ok(db: RbacFakeDb) -> None:
    # Права создаваемой роли ⊆ прав актора → проходит.
    service = _service(db)

    item = await service.create_role(
        RoleCreateRequest(name="Оператор", permissions={"servers": ["view"]}),
        actor_permissions={"servers": ["view", "edit"]},
        actor_privileged=False,
    )
    assert item.name == "Оператор"


@pytest.mark.asyncio
async def test_update_role_non_admin_escalation_is_403(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(
            role.id,
            RoleUpdateRequest(permissions={"servers": ["view", "delete"]}),
            actor_permissions={"servers": ["view"]},
            actor_privileged=False,
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"


@pytest.mark.asyncio
async def test_update_admin_role_by_non_admin_is_403(db: RbacFakeDb) -> None:
    admin_role = db.add_role("admin", full_catalog_permissions())
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(
            admin_role.id,
            RoleUpdateRequest(name="Администраторы"),
            actor_permissions=full_catalog_permissions(),
            actor_privileged=False,
        )
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"


@pytest.mark.asyncio
async def test_update_admin_role_by_privileged_passes(db: RbacFakeDb) -> None:
    admin_role = db.add_role("admin", full_catalog_permissions())
    service = _service(db)

    item = await service.update_role(admin_role.id, RoleUpdateRequest(name="admin"), **_priv())
    assert item.name == "admin"


@pytest.mark.asyncio
async def test_delete_admin_role_by_non_admin_is_403(db: RbacFakeDb) -> None:
    admin_role = db.add_role("admin", full_catalog_permissions())
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_role(admin_role.id, actor_privileged=False)
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"
    assert admin_role.id in db.roles  # не удалена
