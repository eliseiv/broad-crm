"""Тесты TeamService: инвариант «лидер ∈ участники», коды 422/409, замена состава (ADR-022).

Реальный сервис поверх in-memory фейков репозиториев (conftest.RbacFakeDb). Прецеденция
(04-api.md#teams): схемная валидация name (422) → существование leader_id/member_ids
(422 с указанием поля) → уникальность name (409 team_name_taken). Лидер всегда включается
в участники на всех путях записи (create + update, в т.ч. при смене лидера).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.errors import AppError
from app.schemas.team import TeamCreateRequest, TeamUpdateRequest
from app.services.team_service import TeamService
from conftest import RbacFakeDb


@pytest.fixture
def db() -> RbacFakeDb:
    fake = RbacFakeDb()
    fake.add_role("Оператор", {"servers": ["view"]})
    return fake


def _service(db: RbacFakeDb) -> TeamService:
    return TeamService(teams=db.team_repo, users=db.user_repo)


def _user(db: RbacFakeDb, name: str) -> object:
    role = next(iter(db.roles.values()))
    return db.add_user(name, role)


@pytest.mark.asyncio
async def test_create_team_leader_included_and_member_count(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    member = _user(db, "Мария")
    service = _service(db)

    item = await service.create_team(
        TeamCreateRequest(name="Продажи", leader_id=leader.id, member_ids=[member.id])
    )

    assert item.leader_id == leader.id
    assert item.leader_username == "Никита"
    # Лидер + участник; member_count включает лидера.
    assert item.member_count == 2
    assert {m.id for m in item.members} == {leader.id, member.id}


@pytest.mark.asyncio
async def test_create_team_leader_auto_added_when_absent_from_members(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    service = _service(db)

    item = await service.create_team(
        TeamCreateRequest(name="Продажи", leader_id=leader.id, member_ids=[])
    )

    assert item.member_count == 1
    assert {m.id for m in item.members} == {leader.id}


@pytest.mark.asyncio
async def test_create_team_nonexistent_leader_is_422_field_leader_id(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="Продажи", leader_id=uuid.uuid4()))
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "leader_id"


@pytest.mark.asyncio
async def test_create_team_nonexistent_member_is_422_field_member_ids(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_team(
            TeamCreateRequest(name="Продажи", leader_id=leader.id, member_ids=[uuid.uuid4()])
        )
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "member_ids"


@pytest.mark.asyncio
async def test_create_team_invalid_name_is_422_field_name(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="123", leader_id=leader.id))
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "name"


@pytest.mark.asyncio
async def test_create_team_duplicate_name_is_409(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    service = _service(db)
    await service.create_team(TeamCreateRequest(name="Продажи", leader_id=leader.id))

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="Продажи", leader_id=leader.id))
    assert exc.value.status_code == 409
    assert exc.value.code == "team_name_taken"


@pytest.mark.asyncio
async def test_create_team_race_integrity_maps_409(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    db.session.raise_integrity = True
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="Гонка", leader_id=leader.id))
    assert exc.value.code == "team_name_taken"


@pytest.mark.asyncio
async def test_create_team_leader_dup_in_members_is_idempotent(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    service = _service(db)

    item = await service.create_team(
        TeamCreateRequest(name="Продажи", leader_id=leader.id, member_ids=[leader.id])
    )

    assert item.member_count == 1  # дубль лидера в member_ids не удваивает


@pytest.mark.asyncio
async def test_update_team_not_found_is_404(db: RbacFakeDb) -> None:
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_team(uuid.uuid4(), TeamUpdateRequest(name="Новое"))
    assert exc.value.status_code == 404
    assert exc.value.code == "team_not_found"


@pytest.mark.asyncio
async def test_update_team_rename_to_taken_is_409(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    db.add_team("Продажи", leader)
    target = db.add_team("Гость", leader)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_team(target.id, TeamUpdateRequest(name="Продажи"))
    assert exc.value.status_code == 409
    assert exc.value.code == "team_name_taken"


@pytest.mark.asyncio
async def test_update_team_member_ids_full_replace_keeps_leader(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    old_member = _user(db, "Мария")
    new_member = _user(db, "Иван")
    team = db.add_team("Продажи", leader, members=[old_member])
    service = _service(db)

    item = await service.update_team(team.id, TeamUpdateRequest(member_ids=[new_member.id]))

    # Полная замена состава; лидер всегда включён (инвариант).
    assert {m.id for m in item.members} == {leader.id, new_member.id}
    assert old_member.id not in {m.id for m in item.members}


@pytest.mark.asyncio
async def test_update_team_change_leader_adds_to_members(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    member = _user(db, "Мария")
    new_leader = _user(db, "Иван")
    team = db.add_team("Продажи", leader, members=[member])
    service = _service(db)

    item = await service.update_team(team.id, TeamUpdateRequest(leader_id=new_leader.id))

    # Смена лидера без member_ids: прежний состав сохраняется + новый лидер добавлен.
    assert item.leader_id == new_leader.id
    assert {m.id for m in item.members} == {leader.id, member.id, new_leader.id}


@pytest.mark.asyncio
async def test_update_team_change_leader_with_new_members(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    member = _user(db, "Мария")
    new_leader = _user(db, "Иван")
    keep = _user(db, "Ольга")
    team = db.add_team("Продажи", leader, members=[member])
    service = _service(db)

    item = await service.update_team(
        team.id, TeamUpdateRequest(leader_id=new_leader.id, member_ids=[keep.id])
    )

    # Новый состав = member_ids ∪ {новый лидер}; прежние (member) выброшены.
    assert item.leader_id == new_leader.id
    assert {m.id for m in item.members} == {keep.id, new_leader.id}


@pytest.mark.asyncio
async def test_update_team_nonexistent_leader_is_422(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    team = db.add_team("Продажи", leader)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_team(team.id, TeamUpdateRequest(leader_id=uuid.uuid4()))
    assert exc.value.status_code == 422
    assert exc.value.details[0]["field"] == "leader_id"


@pytest.mark.asyncio
async def test_delete_team_then_repeat_is_404(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    team = db.add_team("Продажи", leader)
    service = _service(db)

    await service.delete_team(team.id)
    assert team.id not in db.teams

    with pytest.raises(AppError) as exc:
        await service.delete_team(team.id)
    assert exc.value.status_code == 404
    assert exc.value.code == "team_not_found"


@pytest.mark.asyncio
async def test_list_teams_sorted_created_at_desc(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    first = db.add_team("Первая", leader)
    second = db.add_team("Вторая", leader)
    # Детерминированный порядок (без зависимости от разрешения таймера).
    first.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    second.created_at = datetime(2020, 1, 2, tzinfo=UTC)
    service = _service(db)

    result = await service.list_teams()

    # created_at DESC: более поздняя — первой.
    assert [t.name for t in result.items] == ["Вторая", "Первая"]
