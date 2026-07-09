"""Интеграционные тесты `MailScope` (реальный Postgres) — граница видимости почты.

Проверяют РЕАЛЬНЫЙ резолв `get_mail_scope` (SQL-join `user_teams`→`teams.mail_group_id`,
ADR-038 §3), производный флаг `sees_all_mail_teams` в `GET /api/auth/me` и эндпоинт
`GET /api/teams/{id}/mailboxes` (секция «Почты команды», ленивая загрузка). Внешний
почтовый сервис для эндпоинта команд замокан фейковым `MailService` (реальных запросов
наружу нет); резолв scope БД-запросом — настоящий.
"""

from __future__ import annotations

from typing import Any

from app.api import deps
from app.errors import mail_unavailable
from app.schemas.mail import TeamMailboxesResponse, TeamMailboxItem
from sms_helpers import (
    add_membership,
    build_app,
    build_principal,
    client,
    seed_role,
    seed_team,
    seed_user,
    sms_db,
)


async def _set_group(session: Any, team: Any, group_id: int | None) -> None:
    team.mail_group_id = group_id
    await session.flush()


# --------------------------------------------- get_mail_scope: реальный резолв БД
async def test_scope_superadmin_sees_all_no_db() -> None:
    async with sms_db() as sm, sm() as session:
        scope = await deps.get_mail_scope(build_principal(is_superadmin=True), session)
    assert scope.sees_all_teams is True
    assert scope.group_ids == frozenset()


async def test_scope_full_catalog_role_sees_all() -> None:
    async with sms_db() as sm, sm() as session:
        # Не супер-админ, но полный каталог прав → admin-уровень.
        scope = await deps.get_mail_scope(build_principal(is_superadmin=False), session)
    assert scope.sees_all_teams is True


async def test_scope_partial_role_resolves_group_ids_from_teams() -> None:
    async with sms_db() as sm:
        async with sm() as session:
            role = await seed_role(session, permissions={"mail": ["view"]})
            user = await seed_user(session, role)
            team_a = await seed_team(session, name="A")
            team_b = await seed_team(session, name="B")
            team_c = await seed_team(session, name="C")  # чужая команда
            await _set_group(session, team_a, 3)
            await _set_group(session, team_b, 8)
            await _set_group(session, team_c, 99)
            await add_membership(session, user.id, team_a.id)
            await add_membership(session, user.id, team_b.id)
            await session.commit()
            principal = build_principal(
                user_id=user.id, is_superadmin=False, permissions={"mail": ["view"]}
            )
        async with sm() as session:
            scope = await deps.get_mail_scope(principal, session)

    assert scope.sees_all_teams is False
    assert scope.group_ids == frozenset({3, 8})  # только группы своих команд, не 99


async def test_scope_partial_role_null_group_ids_excluded() -> None:
    """Команда без привязки (mail_group_id=NULL) не даёт группы → пустой scope."""
    async with sms_db() as sm:
        async with sm() as session:
            role = await seed_role(session, permissions={"mail": ["view"]})
            user = await seed_user(session, role)
            team = await seed_team(session, name="A")  # mail_group_id остаётся NULL
            await add_membership(session, user.id, team.id)
            await session.commit()
            principal = build_principal(
                user_id=user.id, is_superadmin=False, permissions={"mail": ["view"]}
            )
        async with sm() as session:
            scope = await deps.get_mail_scope(principal, session)

    assert scope.sees_all_teams is False
    assert scope.group_ids == frozenset()


async def test_scope_user_id_none_is_empty() -> None:
    async with sms_db() as sm, sm() as session:
        principal = build_principal(
            user_id=None, is_superadmin=False, permissions={"mail": ["view"]}
        )
        scope = await deps.get_mail_scope(principal, session)
    assert scope.group_ids == frozenset()


# ----------------------------------------------- GET /api/auth/me sees_all_mail_teams
async def test_me_sees_all_mail_teams_true_for_superadmin() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["sees_all_mail_teams"] is True


async def test_me_sees_all_mail_teams_false_for_partial_catalog() -> None:
    async with sms_db() as sm:
        partial = build_principal(is_superadmin=False, permissions={"mail": ["view"]})
        app = build_app(sm, partial)
        async with client(app) as c:
            resp = await c.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["sees_all_mail_teams"] is False


# --------------------------------------------- GET /api/teams/{id}/mailboxes
class _FakeMailService:
    """Фейк MailService для эндпоинта команд: фиксирует mail_group_id и отдаёт заготовку."""

    def __init__(self, *, response: TeamMailboxesResponse | None = None, raise_502: bool = False):
        self._response = response
        self._raise_502 = raise_502
        self.calls: list[int | None] = []

    async def list_team_mailboxes(self, mail_group_id: int | None) -> TeamMailboxesResponse:
        self.calls.append(mail_group_id)
        if self._raise_502:
            raise mail_unavailable()
        return self._response or TeamMailboxesResponse(mailboxes=[])


async def test_team_mailboxes_no_team_is_404() -> None:
    import uuid

    async with sms_db() as sm:
        fake = _FakeMailService()
        app = build_app(sm, build_principal(), overrides={deps.get_mail_service: lambda: fake})
        async with client(app) as c:
            resp = await c.get(f"/api/teams/{uuid.uuid4()}/mailboxes")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "team_not_found"
    assert fake.calls == []  # mail не дёргается, если команды нет


async def test_team_mailboxes_null_group_empty() -> None:
    async with sms_db() as sm:
        async with sm() as session:
            team = await seed_team(session, name="A")  # mail_group_id NULL
            await session.commit()
            team_id = team.id
        fake = _FakeMailService()
        app = build_app(sm, build_principal(), overrides={deps.get_mail_service: lambda: fake})
        async with client(app) as c:
            resp = await c.get(f"/api/teams/{team_id}/mailboxes")
    assert resp.status_code == 200
    assert resp.json() == {"mailboxes": []}
    assert fake.calls == [None]  # эндпоинт передал mail_group_id=None


async def test_team_mailboxes_success_projects_items() -> None:
    async with sms_db() as sm:
        async with sm() as session:
            team = await seed_team(session, name="A")
            team.mail_group_id = 3
            await session.flush()
            await session.commit()
            team_id = team.id
        response = TeamMailboxesResponse(
            mailboxes=[TeamMailboxItem(id=7, email="inbox@x", display_name="Вх", is_active=True)]
        )
        fake = _FakeMailService(response=response)
        app = build_app(sm, build_principal(), overrides={deps.get_mail_service: lambda: fake})
        async with client(app) as c:
            resp = await c.get(f"/api/teams/{team_id}/mailboxes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mailboxes"][0] == {
        "id": 7,
        "email": "inbox@x",
        "display_name": "Вх",
        "is_active": True,
    }
    assert fake.calls == [3]  # передан mail_group_id команды


async def test_team_mailboxes_external_down_502() -> None:
    async with sms_db() as sm:
        async with sm() as session:
            team = await seed_team(session, name="A")
            team.mail_group_id = 3
            await session.flush()
            await session.commit()
            team_id = team.id
        fake = _FakeMailService(raise_502=True)
        app = build_app(sm, build_principal(), overrides={deps.get_mail_service: lambda: fake})
        async with client(app) as c:
            resp = await c.get(f"/api/teams/{team_id}/mailboxes")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


# ----------------------------------------- teams:view требуется (RBAC-гейт)
async def test_team_mailboxes_requires_teams_view_403() -> None:
    async with sms_db() as sm:
        async with sm() as session:
            team = await seed_team(session, name="A")
            await session.commit()
            team_id = team.id
        principal = build_principal(is_superadmin=False, permissions={"mail": ["view"]})
        fake = _FakeMailService()
        app = build_app(sm, principal, overrides={deps.get_mail_service: lambda: fake})
        async with client(app) as c:
            resp = await c.get(f"/api/teams/{team_id}/mailboxes")
    assert resp.status_code == 403
