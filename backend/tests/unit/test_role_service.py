"""Тесты RoleService: валидация имени/прав, коды 422/409, role_in_use (ADR-021).

Реальный сервис поверх in-memory фейков репозиториев (conftest.RbacFakeDb). Прецеденция
(04-api.md): схемная валидация name (422) → permissions против каталога (422) → уникальность
name (409 role_name_taken). Роль с носителями удалить нельзя → 409 role_in_use.
"""

from __future__ import annotations

import uuid

import pytest
from app.errors import AppError
from app.schemas.role import RoleCreateRequest, RoleUpdateRequest
from app.services.role_service import RoleService
from conftest import RbacFakeDb


@pytest.fixture
def db() -> RbacFakeDb:
    return RbacFakeDb()


def _service(db: RbacFakeDb) -> RoleService:
    return RoleService(repository=db.role_repo)


@pytest.mark.asyncio
async def test_create_role_valid(db: RbacFakeDb) -> None:
    service = _service(db)

    item = await service.create_role(
        RoleCreateRequest(name="Оператор", permissions={"servers": ["view"], "mail": ["view"]})
    )

    assert item.name == "Оператор"
    assert item.permissions == {"servers": ["view"], "mail": ["view"]}


@pytest.mark.asyncio
async def test_create_role_invalid_name_is_422_field_name(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(RoleCreateRequest(name="123", permissions={}))
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "name"


@pytest.mark.asyncio
async def test_create_role_permissions_out_of_catalog_is_422_field_permissions(
    db: RbacFakeDb,
) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(
            RoleCreateRequest(name="Оператор", permissions={"servers": ["fly"]})
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "permissions"


@pytest.mark.asyncio
async def test_create_role_rejects_users_page_in_permissions(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(
            RoleCreateRequest(name="Оператор", permissions={"users": ["view"]})
        )
    assert exc.value.details[0]["field"] == "permissions"


@pytest.mark.asyncio
async def test_create_role_precedence_name_before_permissions(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(RoleCreateRequest(name="...", permissions={"servers": ["fly"]}))
    assert exc.value.details[0]["field"] == "name"


@pytest.mark.asyncio
async def test_create_role_duplicate_name_is_409(db: RbacFakeDb) -> None:
    service = _service(db)
    await service.create_role(RoleCreateRequest(name="Оператор", permissions={}))

    with pytest.raises(AppError) as exc:
        await service.create_role(RoleCreateRequest(name="Оператор", permissions={}))
    assert exc.value.status_code == 409
    assert exc.value.code == "role_name_taken"


@pytest.mark.asyncio
async def test_create_role_race_integrity_maps_409(db: RbacFakeDb) -> None:
    db.session.raise_integrity = True
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_role(RoleCreateRequest(name="Гонка", permissions={}))
    assert exc.value.code == "role_name_taken"


@pytest.mark.asyncio
async def test_update_role_not_found_is_404(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(uuid.uuid4(), RoleUpdateRequest(name="Новое"))
    assert exc.value.status_code == 404
    assert exc.value.code == "role_not_found"


@pytest.mark.asyncio
async def test_update_role_rename_to_taken_is_409(db: RbacFakeDb) -> None:
    db.add_role("Оператор", {})
    target = db.add_role("Гость", {})
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(target.id, RoleUpdateRequest(name="Оператор"))
    assert exc.value.status_code == 409
    assert exc.value.code == "role_name_taken"


@pytest.mark.asyncio
async def test_update_role_permissions_out_of_catalog_is_422(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_role(role.id, RoleUpdateRequest(permissions={"backends": ["nope"]}))
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "permissions"


@pytest.mark.asyncio
async def test_update_role_applies_name_and_permissions(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    service = _service(db)

    item = await service.update_role(
        role.id,
        RoleUpdateRequest(name="Супероператор", permissions={"servers": ["view", "edit"]}),
    )

    assert item.name == "Супероператор"
    assert item.permissions == {"servers": ["view", "edit"]}


@pytest.mark.asyncio
async def test_delete_role_in_use_is_409(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {"servers": ["view"]})
    db.add_user("Никита", role)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_role(role.id)
    assert exc.value.status_code == 409
    assert exc.value.code == "role_in_use"
    assert role.id in db.roles  # не удалена


@pytest.mark.asyncio
async def test_delete_role_not_found_is_404(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_role(uuid.uuid4())
    assert exc.value.status_code == 404
    assert exc.value.code == "role_not_found"


@pytest.mark.asyncio
async def test_delete_role_success(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {})
    service = _service(db)

    await service.delete_role(role.id)

    assert role.id not in db.roles


@pytest.mark.asyncio
async def test_delete_role_race_integrity_maps_409(db: RbacFakeDb) -> None:
    role = db.add_role("Оператор", {})
    db.session.raise_integrity = True
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_role(role.id)
    assert exc.value.code == "role_in_use"


@pytest.mark.asyncio
async def test_list_roles_returns_all(db: RbacFakeDb) -> None:
    db.add_role("admin", {})
    db.add_role("Оператор", {})
    service = _service(db)

    result = await service.list_roles()

    assert {r.name for r in result.items} == {"admin", "Оператор"}
