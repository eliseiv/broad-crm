"""Тесты UserService: прецеденция ошибок, bcrypt-хэш, пароль не в ответах (ADR-021).

Прогоняется реальный сервис поверх in-memory фейков репозиториев (conftest.RbacFakeDb),
что сохраняет установленную в репо конвенцию тестов без Postgres. Прецеденция (04-api.md):
username-формат (422) → существование role_id (422) → уникальность username (409).
"""

from __future__ import annotations

import uuid

import pytest
from app.domain.permissions import full_catalog_permissions
from app.errors import AppError
from app.infra.passwords import verify_password
from app.schemas.user import UserCreateRequest, UserUpdateRequest
from app.services.user_service import UserService
from conftest import RbacFakeDb


@pytest.fixture
def db() -> RbacFakeDb:
    fake = RbacFakeDb()
    fake.add_role("admin", full_catalog_permissions())
    return fake


def _service(db: RbacFakeDb) -> UserService:
    return UserService(users=db.user_repo, roles=db.role_repo, teams=db.team_repo)


@pytest.mark.asyncio
async def test_create_user_hashes_password_and_omits_it_from_response(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    item = await service.create_user(
        UserCreateRequest(username="Никита", password="s3cret-pass", role_id=role.id)
    )

    assert item.username == "Никита"
    assert item.role_id == role.id
    assert item.role_name == "admin"
    assert item.is_active is True
    # Пароль (plaintext/hash) отсутствует в схеме ответа.
    assert not hasattr(item, "password")
    assert not hasattr(item, "password_hash")
    # В хранилище — bcrypt-хэш, верифицируемый исходным паролем.
    stored = next(iter(db.users.values()))
    assert stored.password_hash != "s3cret-pass"
    assert verify_password("s3cret-pass", stored.password_hash) is True


@pytest.mark.asyncio
async def test_create_user_invalid_username_is_422(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(username="123", password="s3cret-pass", role_id=role.id)
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"
    assert exc.value.details[0]["field"] == "username"


@pytest.mark.asyncio
async def test_create_user_missing_role_is_422_field_role_id(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(username="Никита", password="s3cret-pass", role_id=uuid.uuid4())
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "role_id"


@pytest.mark.asyncio
async def test_create_user_precedence_username_before_role(db: RbacFakeDb) -> None:
    # И username невалиден, И role отсутствует → сначала 422 username (прецеденция).
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(username="...", password="s3cret-pass", role_id=uuid.uuid4())
        )
    assert exc.value.details[0]["field"] == "username"


@pytest.mark.asyncio
async def test_create_user_duplicate_username_is_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)
    await service.create_user(
        UserCreateRequest(username="Никита", password="s3cret-pass", role_id=role.id)
    )

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(username="Никита", password="other-pass", role_id=role.id)
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "username_taken"


@pytest.mark.asyncio
async def test_create_user_race_integrity_error_maps_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    db.session.raise_integrity = True  # гонка уникальности на commit
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(username="Гонка", password="s3cret-pass", role_id=role.id)
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "username_taken"


@pytest.mark.asyncio
async def test_update_user_not_found_is_404(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_user(uuid.uuid4(), UserUpdateRequest(is_active=False))
    assert exc.value.status_code == 404
    assert exc.value.code == "user_not_found"


@pytest.mark.asyncio
async def test_update_user_empty_password_is_422(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role, password_hash="orig")
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_user(user.id, UserUpdateRequest(password=""))
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "password"
    assert user.password_hash == "orig"  # пароль не тронут


@pytest.mark.asyncio
async def test_update_user_resets_password_and_toggles_status_and_role(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    other = db.add_role("Оператор", {"servers": ["view"]})
    user = db.add_user("Иван", role, password_hash="orig")
    service = _service(db)

    item = await service.update_user(
        user.id,
        UserUpdateRequest(password="brand-new-pass", is_active=False, role_id=other.id),
    )

    assert item.is_active is False
    assert item.role_id == other.id
    assert item.role_name == "Оператор"
    assert verify_password("brand-new-pass", user.password_hash) is True


@pytest.mark.asyncio
async def test_update_user_missing_role_is_422(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_user(user.id, UserUpdateRequest(role_id=uuid.uuid4()))
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "role_id"


@pytest.mark.asyncio
async def test_delete_user_then_repeat_is_404(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role)
    service = _service(db)

    await service.delete_user(user.id)
    assert user.id not in db.users

    with pytest.raises(AppError) as exc:
        await service.delete_user(user.id)
    assert exc.value.status_code == 404
    assert exc.value.code == "user_not_found"


@pytest.mark.asyncio
async def test_list_users_returns_all(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    db.add_user("Иван", role)
    db.add_user("Пётр", role)
    service = _service(db)

    result = await service.list_users()

    assert {u.username for u in result.items} == {"Иван", "Пётр"}
    assert all(not hasattr(u, "password_hash") for u in result.items)


# --- email (опциональный, ADR-022) ---


@pytest.mark.asyncio
async def test_create_user_with_email_is_normalized(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    item = await service.create_user(
        UserCreateRequest(
            username="Никита",
            email="  Nikita@Example.COM ",
            password="s3cret-pass",
            role_id=role.id,
        )
    )

    # Нормализация trim+lower.
    assert item.email == "nikita@example.com"


@pytest.mark.asyncio
async def test_create_user_without_email_is_none(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    item = await service.create_user(
        UserCreateRequest(username="Никита", password="s3cret-pass", role_id=role.id)
    )

    assert item.email is None


@pytest.mark.asyncio
async def test_create_user_invalid_email_is_422_field_email(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(
                username="Никита", email="not-an-email", password="s3cret-pass", role_id=role.id
            )
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "email"


@pytest.mark.asyncio
async def test_create_user_duplicate_email_is_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)
    await service.create_user(
        UserCreateRequest(
            username="Никита", email="dup@example.com", password="s3cret-pass", role_id=role.id
        )
    )

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(
                username="Пётр", email="DUP@example.com", password="other-pass", role_id=role.id
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "email_taken"


@pytest.mark.asyncio
async def test_update_user_clear_email_via_null(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role, email="ivan@example.com")
    service = _service(db)

    item = await service.update_user(user.id, UserUpdateRequest(email=None))

    assert item.email is None


@pytest.mark.asyncio
async def test_update_user_clear_email_via_empty_string(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role, email="ivan@example.com")
    service = _service(db)

    item = await service.update_user(user.id, UserUpdateRequest(email=""))

    assert item.email is None


@pytest.mark.asyncio
async def test_update_user_set_email_duplicate_is_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    db.add_user("Пётр", role, email="taken@example.com")
    user = db.add_user("Иван", role)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_user(user.id, UserUpdateRequest(email="taken@example.com"))
    assert exc.value.status_code == 409
    assert exc.value.code == "email_taken"


# --- team_ids (CRM-команды, ADR-022) ---


@pytest.mark.asyncio
async def test_create_user_nonexistent_team_id_is_422_field_team_ids(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(
                username="Никита",
                password="s3cret-pass",
                role_id=role.id,
                team_ids=[uuid.uuid4()],
            )
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "team_ids"


@pytest.mark.asyncio
async def test_create_user_with_team_ids_reflected_in_response(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    leader = db.add_user("Лидер", role)
    team = db.add_team("Продажи", leader)
    service = _service(db)

    item = await service.create_user(
        UserCreateRequest(
            username="Никита", password="s3cret-pass", role_id=role.id, team_ids=[team.id]
        )
    )

    assert [t.id for t in item.teams] == [team.id]
    assert item.teams[0].name == "Продажи"


@pytest.mark.asyncio
async def test_update_user_team_ids_full_replace(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    leader = db.add_user("Лидер", role)
    team_a = db.add_team("A", leader)
    team_b = db.add_team("B", leader)
    user = db.add_user("Иван", role)
    service = _service(db)

    await service.update_user(user.id, UserUpdateRequest(team_ids=[team_a.id]))
    item = await service.update_user(user.id, UserUpdateRequest(team_ids=[team_b.id]))

    # Полная замена: остаётся только B.
    assert {t.id for t in item.teams} == {team_b.id}


@pytest.mark.asyncio
async def test_update_user_leader_preserved_when_excluded_from_team_ids(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role)
    led = db.add_team("Продажи", user)  # user — лидер команды
    service = _service(db)

    # Пытаемся исключить пользователя из всех команд — но он лидер `led`.
    item = await service.update_user(user.id, UserUpdateRequest(team_ids=[]))

    # Инвариант «лидер ∈ участники»: команда, ведомая пользователем, сохраняется.
    assert led.id in {t.id for t in item.teams}
    assert user.id in db.teams[led.id]._member_ids


@pytest.mark.asyncio
async def test_delete_user_who_is_team_leader_is_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role)
    db.add_team("Продажи", user)
    # ON DELETE RESTRICT на teams.leader_id → IntegrityError на commit → 409.
    db.session.raise_integrity = True
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.delete_user(user.id)
    assert exc.value.status_code == 409
    assert exc.value.code == "user_is_team_leader"
