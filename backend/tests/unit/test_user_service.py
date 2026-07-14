"""Тесты UserService: прецеденция ошибок, bcrypt-хэш, telegram, беспарольность, команды.

Прогоняется реальный сервис поверх in-memory фейков репозиториев (conftest.RbacFakeDb),
что сохраняет установленную в репо конвенцию тестов без Postgres. Прецеденция (04-api.md):
username-формат (422) → telegram-формат (422) → существование role_id/team_ids (422) →
уникальность username (409) → уникальность telegram (409). Пароль опционален (ADR-025);
при исключении из ведомой команды лидерство авто-передаётся (ADR-026).
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
    return UserService(
        users=db.user_repo,
        roles=db.role_repo,
        teams=db.team_repo,
        channels=db.channel_repo,
    )


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
    assert item.has_password is True
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


# --- Беспарольные пользователи (ADR-025) ---


@pytest.mark.asyncio
async def test_create_user_without_password_is_passwordless(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    item = await service.create_user(UserCreateRequest(username="Никита", role_id=role.id))

    assert item.has_password is False
    stored = next(iter(db.users.values()))
    assert stored.password_hash is None


@pytest.mark.asyncio
async def test_create_user_empty_password_is_passwordless(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    item = await service.create_user(
        UserCreateRequest(username="Никита", password="", role_id=role.id)
    )

    assert item.has_password is False


@pytest.mark.asyncio
async def test_create_user_short_password_is_422(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(username="Никита", password="short", role_id=role.id)
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "password"


# --- telegram (опциональный, ADR-025) ---


@pytest.mark.asyncio
async def test_create_user_with_telegram_is_normalized(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    item = await service.create_user(
        UserCreateRequest(
            username="Никита",
            telegram="@Nikita_01",
            password="s3cret-pass",
            role_id=role.id,
        )
    )

    # Нормализация: снят ведущий @, lower-case.
    assert item.telegram == "nikita_01"


@pytest.mark.asyncio
async def test_create_user_without_telegram_is_none(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    item = await service.create_user(
        UserCreateRequest(username="Никита", password="s3cret-pass", role_id=role.id)
    )

    assert item.telegram is None


@pytest.mark.asyncio
async def test_create_user_invalid_telegram_is_422_field_telegram(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(
                username="Никита", telegram="bad ник", password="s3cret-pass", role_id=role.id
            )
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "telegram"


@pytest.mark.asyncio
async def test_create_user_duplicate_telegram_is_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    service = _service(db)
    await service.create_user(
        UserCreateRequest(
            username="Никита", telegram="dup_nick", password="s3cret-pass", role_id=role.id
        )
    )

    with pytest.raises(AppError) as exc:
        await service.create_user(
            UserCreateRequest(
                username="Пётр", telegram="@DUP_nick", password="other-pass", role_id=role.id
            )
        )
    assert exc.value.status_code == 409
    assert exc.value.code == "telegram_taken"


@pytest.mark.asyncio
async def test_update_user_clear_telegram_via_null(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role, telegram="ivan_nick")
    service = _service(db)

    item = await service.update_user(user.id, UserUpdateRequest(telegram=None))

    assert item.telegram is None


@pytest.mark.asyncio
async def test_update_user_clear_telegram_via_empty_string(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    user = db.add_user("Иван", role, telegram="ivan_nick")
    service = _service(db)

    item = await service.update_user(user.id, UserUpdateRequest(telegram=""))

    assert item.telegram is None


@pytest.mark.asyncio
async def test_update_user_set_telegram_duplicate_is_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    db.add_user("Пётр", role, telegram="taken_nick")
    user = db.add_user("Иван", role)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_user(user.id, UserUpdateRequest(telegram="@Taken_nick"))
    assert exc.value.status_code == 409
    assert exc.value.code == "telegram_taken"


# --- team_ids (CRM-команды, ADR-022/026) ---


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
async def test_update_user_excluded_leader_auto_transfers(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    leader = db.add_user("Иван", role)
    heir = db.add_user("Мария", role)
    led = db.add_team("Продажи", leader, members=[heir])  # Иван — лидер, Мария — участник
    service = _service(db)

    # Иван исключается из всех команд (team_ids=[]) → лидерство переходит Марии (ADR-026).
    item = await service.update_user(leader.id, UserUpdateRequest(team_ids=[]))

    assert led.id not in {t.id for t in item.teams}  # Иван больше не в команде
    assert db.teams[led.id].leader_id == heir.id  # авто-передача Марии
    assert leader.id not in db.teams[led.id]._members


@pytest.mark.asyncio
async def test_update_user_excluded_last_leader_leaves_team_leaderless(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    leader = db.add_user("Иван", role)
    led = db.add_team("Продажи", leader)  # единственный участник и лидер
    service = _service(db)

    await service.update_user(leader.id, UserUpdateRequest(team_ids=[]))

    assert db.teams[led.id].leader_id is None  # участников не осталось → без лидера


@pytest.mark.asyncio
async def test_delete_user_leader_auto_transfers_no_409(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    leader = db.add_user("Иван", role)
    heir = db.add_user("Мария", role)
    led = db.add_team("Продажи", leader, members=[heir])
    service = _service(db)

    # Удаление пользователя-лидера завершается успешно (204) с авто-передачей (ADR-026,
    # код 409 user_is_team_leader упразднён).
    await service.delete_user(leader.id)

    assert leader.id not in db.users
    assert db.teams[led.id].leader_id == heir.id


@pytest.mark.asyncio
async def test_delete_last_leader_leaves_team_leaderless(db: RbacFakeDb) -> None:
    role = next(iter(db.roles.values()))
    leader = db.add_user("Иван", role)
    led = db.add_team("Продажи", leader)
    service = _service(db)

    await service.delete_user(leader.id)

    assert leader.id not in db.users
    assert db.teams[led.id].leader_id is None
