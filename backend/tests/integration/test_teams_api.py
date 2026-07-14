"""Контрактные тесты Teams API (04-api.md#teams, ADR-022).

Реальный роутер + `TeamService` поверх in-memory фейков репозиториев (conftest.RbacFakeDb),
`get_current_principal` замокан. Проверяются CRUD, гейт матрицы `teams:*` (403), коды и
прецеденция ошибок (409 team_name_taken, 422 leader_id/member_ids), инвариант
«лидер ∈ участники» и `member_count` (включает лидера).
"""

from __future__ import annotations

from typing import Any

import pytest
from app.api import deps
from app.services.team_service import TeamService
from conftest import RbacFakeDb, make_principal
from httpx import ASGITransport, AsyncClient


def _build_app(db: RbacFakeDb, principal: Any) -> Any:
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())
    app.dependency_overrides[deps.get_current_principal] = lambda: principal
    app.dependency_overrides[deps.get_team_service] = lambda: TeamService(
        teams=db.team_repo,
        users=db.user_repo,
        numbers=db.number_repo,
        mailboxes=db.mailbox_repo,
        channels=db.channel_repo,
    )
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _seed_users(db: RbacFakeDb, *names: str) -> list[Any]:
    role = db.add_role("Оператор", {"servers": ["view"]})
    return [db.add_user(name, role) for name in names]


@pytest.mark.asyncio
async def test_teams_crud_contract() -> None:
    db = RbacFakeDb()
    leader, member = _seed_users(db, "Никита", "Мария")
    app = _build_app(db, make_principal())  # супер-админ

    async with _client(app) as client:
        created = await client.post(
            "/api/teams",
            json={
                "name": "Продажи",
                "leader_id": str(leader.id),
                "member_ids": [str(member.id)],
            },
        )
        listed = await client.get("/api/teams")
        team_id = created.json()["id"]
        patched = await client.patch(f"/api/teams/{team_id}", json={"name": "Продажи EU"})
        deleted = await client.delete(f"/api/teams/{team_id}")
        repeat = await client.delete(f"/api/teams/{team_id}")

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Продажи"
    assert body["leader_id"] == str(leader.id)
    assert body["leader_username"] == "Никита"
    # member_count включает лидера; лидер присутствует в members (инвариант).
    assert body["member_count"] == 2
    # number_count — новый агрегат SMS-номеров команды (ADR-030); без номеров = 0.
    assert body["number_count"] == 0
    assert {m["id"] for m in body["members"]} == {str(leader.id), str(member.id)}
    assert listed.json()["items"][0]["number_count"] == 0
    assert listed.status_code == 200
    assert [t["name"] for t in listed.json()["items"]] == ["Продажи"]
    assert patched.status_code == 200
    assert patched.json()["name"] == "Продажи EU"
    assert deleted.status_code == 204
    assert repeat.status_code == 404
    assert repeat.json()["error"]["code"] == "team_not_found"


@pytest.mark.asyncio
async def test_teams_create_leader_auto_member() -> None:
    db = RbacFakeDb()
    (leader,) = _seed_users(db, "Никита")
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        created = await client.post(
            "/api/teams", json={"name": "Продажи", "leader_id": str(leader.id)}
        )

    assert created.status_code == 201
    body = created.json()
    assert body["member_count"] == 1
    assert {m["id"] for m in body["members"]} == {str(leader.id)}


@pytest.mark.asyncio
async def test_teams_duplicate_name_is_409() -> None:
    db = RbacFakeDb()
    (leader,) = _seed_users(db, "Никита")
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        await client.post("/api/teams", json={"name": "Продажи", "leader_id": str(leader.id)})
        dup = await client.post("/api/teams", json={"name": "Продажи", "leader_id": str(leader.id)})

    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "team_name_taken"


@pytest.mark.asyncio
async def test_teams_nonexistent_leader_is_422() -> None:
    db = RbacFakeDb()
    _seed_users(db, "Никита")
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        resp = await client.post(
            "/api/teams",
            json={"name": "Продажи", "leader_id": "00000000-0000-0000-0000-0000000000aa"},
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "leader_id"


@pytest.mark.asyncio
async def test_teams_nonexistent_member_is_422() -> None:
    db = RbacFakeDb()
    (leader,) = _seed_users(db, "Никита")
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        resp = await client.post(
            "/api/teams",
            json={
                "name": "Продажи",
                "leader_id": str(leader.id),
                "member_ids": ["00000000-0000-0000-0000-0000000000bb"],
            },
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "member_ids"


@pytest.mark.asyncio
async def test_teams_patch_change_leader_keeps_invariant() -> None:
    db = RbacFakeDb()
    leader, member, new_leader = _seed_users(db, "Никита", "Мария", "Иван")
    team = db.add_team("Продажи", leader, members=[member])
    app = _build_app(db, make_principal())

    async with _client(app) as client:
        resp = await client.patch(f"/api/teams/{team.id}", json={"leader_id": str(new_leader.id)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["leader_id"] == str(new_leader.id)
    # Новый лидер обязательно в участниках (инвариант); прежний состав сохранён.
    assert str(new_leader.id) in {m["id"] for m in body["members"]}
    assert body["member_count"] == 3


@pytest.mark.asyncio
async def test_teams_gate_view_forbidden_without_permission() -> None:
    db = RbacFakeDb()
    operator = make_principal(
        is_superadmin=False, role="Оператор", permissions={"servers": ["view"]}
    )
    app = _build_app(db, operator)

    async with _client(app) as client:
        resp = await client.get("/api/teams")

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_teams_gate_create_forbidden_with_only_view() -> None:
    db = RbacFakeDb()
    (leader,) = _seed_users(db, "Никита")
    viewer = make_principal(is_superadmin=False, role="Оператор", permissions={"teams": ["view"]})
    app = _build_app(db, viewer)

    async with _client(app) as client:
        resp = await client.post(
            "/api/teams", json={"name": "Продажи", "leader_id": str(leader.id)}
        )

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.asyncio
async def test_teams_view_permission_allows_list() -> None:
    db = RbacFakeDb()
    viewer = make_principal(is_superadmin=False, role="Оператор", permissions={"teams": ["view"]})
    app = _build_app(db, viewer)

    async with _client(app) as client:
        resp = await client.get("/api/teams")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}
