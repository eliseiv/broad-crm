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
    return TeamService(
        teams=db.team_repo,
        users=db.user_repo,
        numbers=db.number_repo,
        mailboxes=db.mailbox_repo,
    )


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
async def test_update_team_member_ids_full_replace_excluded_leader_transfers(
    db: RbacFakeDb,
) -> None:
    leader = _user(db, "Никита")
    old_member = _user(db, "Мария")
    new_member = _user(db, "Иван")
    team = db.add_team("Продажи", leader, members=[old_member])
    service = _service(db)

    # Полная замена состава на [new_member]; лидер (Никита) исключён, leader_id не задан →
    # лидерство авто-передаётся оставшемуся участнику (ADR-026), прежний состав выброшен.
    item = await service.update_team(team.id, TeamUpdateRequest(member_ids=[new_member.id]))

    assert {m.id for m in item.members} == {new_member.id}
    assert item.leader_id == new_member.id
    assert old_member.id not in {m.id for m in item.members}
    assert leader.id not in {m.id for m in item.members}


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


# --- Опциональный лидер, авто-назначение и авто-передача (ADR-026) ---


@pytest.mark.asyncio
async def test_create_empty_team_without_leader(db: RbacFakeDb) -> None:
    service = _service(db)

    item = await service.create_team(TeamCreateRequest(name="Пустая"))

    assert item.leader_id is None
    assert item.leader_username is None
    assert item.member_count == 0
    assert item.members == []


@pytest.mark.asyncio
async def test_create_team_first_member_becomes_leader(db: RbacFakeDb) -> None:
    first = _user(db, "Никита")
    second = _user(db, "Мария")
    service = _service(db)

    # Лидер не задан, есть участники → лидером становится первый (member_ids[0]).
    item = await service.create_team(
        TeamCreateRequest(name="Продажи", member_ids=[first.id, second.id])
    )

    assert item.leader_id == first.id
    assert item.member_count == 2


@pytest.mark.asyncio
async def test_update_team_remove_leader_via_null(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    team = db.add_team("Продажи", leader)
    service = _service(db)

    # leader_id=null → снятие лидера (команда без лидера), состав не тронут.
    item = await service.update_team(team.id, TeamUpdateRequest(leader_id=None))

    assert item.leader_id is None
    assert item.leader_username is None


@pytest.mark.asyncio
async def test_update_team_excluded_leader_auto_transfers_by_created_at(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    heir = _user(db, "Мария")
    late = _user(db, "Иван")
    # Порядок добавления (created_at): heir раньше late; лидер — Никита.
    team = db.add_team("Продажи", leader, members=[heir, late])
    service = _service(db)

    # Новый состав без текущего лидера, leader_id не передан → авто-передача
    # старейшему из оставшихся по user_teams.created_at (heir добавлен раньше late).
    item = await service.update_team(team.id, TeamUpdateRequest(member_ids=[late.id, heir.id]))

    assert item.leader_id == heir.id  # старейший по дате, НЕ первый в массиве
    assert leader.id not in {m.id for m in item.members}


@pytest.mark.asyncio
async def test_update_team_excluded_leader_no_members_left_is_leaderless(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    team = db.add_team("Продажи", leader)
    service = _service(db)

    # Новый состав пуст, лидер исключён → команда без лидера.
    item = await service.update_team(team.id, TeamUpdateRequest(member_ids=[]))

    assert item.leader_id is None
    assert item.member_count == 0


@pytest.mark.asyncio
async def test_update_leaderless_team_add_members_assigns_first_leader(db: RbacFakeDb) -> None:
    first = _user(db, "Никита")
    second = _user(db, "Мария")
    team = db.add_team("Пустая")  # без лидера, без участников
    service = _service(db)

    item = await service.update_team(team.id, TeamUpdateRequest(member_ids=[first.id, second.id]))

    # У команды не было лидера + добавлены участники → первый становится лидером.
    assert item.leader_id == first.id


# --- Привязка к группе mail-агрегатора (ADR-038): 409 team_mail_group_taken ---


@pytest.mark.asyncio
async def test_create_team_free_mail_group_ok(db: RbacFakeDb) -> None:
    service = _service(db)

    item = await service.create_team(TeamCreateRequest(name="Продажи", mail_group_id=5))

    assert item.mail_group_id == 5


@pytest.mark.asyncio
async def test_create_team_mail_group_taken_is_409(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    db.add_team("Существующая", leader, mail_group_id=3)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="Новая", mail_group_id=3))
    assert exc.value.status_code == 409
    assert exc.value.code == "team_mail_group_taken"


@pytest.mark.asyncio
async def test_create_team_race_mail_group_structured_constraint_maps_taken(
    db: RbacFakeDb,
) -> None:
    """Гонка: pre-check прошёл, на commit IntegrityError с именем констрейнта
    `uq_teams_mail_group_id` → 409 team_mail_group_taken (не team_name_taken)."""
    service = _service(db)
    db.session.raise_integrity = True
    db.session.integrity_constraint = "uq_teams_mail_group_id"

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="Проигравший", mail_group_id=7))
    assert exc.value.code == "team_mail_group_taken"


@pytest.mark.asyncio
async def test_create_team_race_mail_group_fallback_recheck_maps_taken(db: RbacFakeDb) -> None:
    """Драйвер не отдал constraint_name → фолбэк перепроверяет занятость группы
    (победитель гонки уже закоммичен) и отдаёт team_mail_group_taken."""
    service = _service(db)
    calls = {"n": 0}

    async def flaky(mail_group_id: int, *, exclude_id: object = None) -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # pre-check — свободно; фолбэк-перепроверка — занято

    db.team_repo.exists_by_mail_group_id = flaky  # type: ignore[assignment]
    db.session.raise_integrity = True  # integrity_constraint=None → фолбэк

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="Проигравший", mail_group_id=7))
    assert exc.value.code == "team_mail_group_taken"


@pytest.mark.asyncio
async def test_create_team_race_name_conflict_still_maps_name_taken(db: RbacFakeDb) -> None:
    """Гонка без группы (mail_group_id=None) → фолбэк не срабатывает → team_name_taken."""
    leader = _user(db, "Никита")
    db.session.raise_integrity = True
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.create_team(TeamCreateRequest(name="Гонка", leader_id=leader.id))
    assert exc.value.code == "team_name_taken"


@pytest.mark.asyncio
async def test_update_team_mail_group_taken_is_409(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    db.add_team("Занявшая", leader, mail_group_id=3)
    target = db.add_team("Цель", leader)
    service = _service(db)

    with pytest.raises(AppError) as exc:
        await service.update_team(target.id, TeamUpdateRequest(mail_group_id=3))
    assert exc.value.status_code == 409
    assert exc.value.code == "team_mail_group_taken"


@pytest.mark.asyncio
async def test_update_team_set_and_clear_mail_group(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    team = db.add_team("Продажи", leader)
    service = _service(db)

    linked = await service.update_team(team.id, TeamUpdateRequest(mail_group_id=8))
    assert linked.mail_group_id == 8

    cleared = await service.update_team(team.id, TeamUpdateRequest(mail_group_id=None))
    assert cleared.mail_group_id is None


@pytest.mark.asyncio
async def test_update_team_leader_kept_when_still_member(db: RbacFakeDb) -> None:
    leader = _user(db, "Никита")
    member = _user(db, "Мария")
    add = _user(db, "Иван")
    team = db.add_team("Продажи", leader, members=[member])
    service = _service(db)

    # Лидер остаётся в новом составе → лидерство не меняется.
    item = await service.update_team(team.id, TeamUpdateRequest(member_ids=[leader.id, add.id]))

    assert item.leader_id == leader.id
    assert {m.id for m in item.members} == {leader.id, add.id}


# --- mailbox_count (ADR-048 §1) ---------------------------------------------


@pytest.mark.asyncio
async def test_list_teams_mailbox_count_is_zero_for_team_without_mailboxes(
    db: RbacFakeDb,
) -> None:
    """Команда без ящиков → `mailbox_count = 0` (не null, не пропуск ключа; ADR-048 §1)."""
    leader = _user(db, "Никита")
    db.add_team("Продажи", leader)
    service = _service(db)

    items = (await service.list_teams()).items

    assert [t.mailbox_count for t in items] == [0]


@pytest.mark.asyncio
async def test_list_teams_mailbox_count_counts_only_own_mailboxes(db: RbacFakeDb) -> None:
    """Батч-агрегат считает ящики по своей команде; чужие/unassigned не попадают."""
    leader = _user(db, "Никита")
    sales = db.add_team("Продажи", leader)
    support = db.add_team("Поддержка", leader)
    db.add_mailbox("a@example.com", team=sales)
    db.add_mailbox("b@example.com", team=sales)
    db.add_mailbox("c@example.com", team=support)
    db.add_mailbox("unassigned@example.com", team=None)
    service = _service(db)

    counts = {t.name: t.mailbox_count for t in (await service.list_teams()).items}

    assert counts == {"Продажи": 2, "Поддержка": 1}


@pytest.mark.asyncio
async def test_create_team_body_has_mailbox_count_zero(db: RbacFakeDb) -> None:
    """Тело 201 содержит `mailbox_count` (у новой команды ящиков нет → 0)."""
    leader = _user(db, "Никита")
    service = _service(db)

    item = await service.create_team(TeamCreateRequest(name="Продажи", leader_id=leader.id))

    assert item.mailbox_count == 0


@pytest.mark.asyncio
async def test_update_team_body_has_current_mailbox_count(db: RbacFakeDb) -> None:
    """Тело 200 PATCH содержит актуальный `mailbox_count` (одиночный агрегат)."""
    leader = _user(db, "Никита")
    team = db.add_team("Продажи", leader)
    db.add_mailbox("a@example.com", team=team)
    db.add_mailbox("b@example.com", team=team)
    service = _service(db)

    item = await service.update_team(team.id, TeamUpdateRequest(name="Продажи EU"))

    assert item.mailbox_count == 2


@pytest.mark.asyncio
async def test_mailbox_count_recomputed_after_mailbox_transferred_to_other_team(
    db: RbacFakeDb,
) -> None:
    """Перенос ящика в другую команду → счётчики пересчитываются (агрегат на лету)."""
    leader = _user(db, "Никита")
    sales = db.add_team("Продажи", leader)
    support = db.add_team("Поддержка", leader)
    mailbox = db.add_mailbox("a@example.com", team=sales)
    service = _service(db)

    before = {t.name: t.mailbox_count for t in (await service.list_teams()).items}
    mailbox.team_id = support.id
    after = {t.name: t.mailbox_count for t in (await service.list_teams()).items}

    assert before == {"Продажи": 1, "Поддержка": 0}
    assert after == {"Продажи": 0, "Поддержка": 1}
