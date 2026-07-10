"""Integration S3 (ADR-044 §2/§4/§7): лента писем, keyset-пагинация, анти-энумерация.

Реальный Postgres + FastAPI-app; агрегатор к чтению не привлекается. Проверяет:
компаундный keyset при СОВПАДАЮЩИХ `internal_date` (нет пропусков/дублей — MINOR-2),
`invalid_cursor`/`invalid_limit`, анти-энумерацию не-админа (пустой scope → пустая
страница БЕЗ запроса ленты в БД и без вызова агрегатора), чужой фильтр → пусто (не 403),
scope-фильтр списка ящиков. Коды сверяются по `error.code`.
"""

from __future__ import annotations

from typing import Any

import pytest
from mail_s34_helpers import (
    FakeMailClient,
    add_membership,
    build_app,
    build_principal,
    client,
    dt,
    mail_db,
    seed_account,
    seed_message,
    seed_role,
    seed_team,
    seed_user,
)


async def _collect_all_pages(c: Any, params: dict[str, Any]) -> list[int]:
    """Пройти все страницы ленты по next_cursor, вернуть плоский список id (в порядке)."""
    ids: list[int] = []
    before: str | None = None
    for _ in range(50):  # предохранитель от бесконечного цикла
        q = dict(params)
        if before is not None:
            q["before"] = before
        resp = await c.get("/api/mail/messages", params=q)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids.extend(m["id"] for m in body["messages"])
        before = body["next_cursor"]
        if before is None:
            break
    return ids


# --- Keyset при совпадающих internal_date (критично, MINOR-2) -----------------
async def test_keyset_same_internal_date_no_gaps_no_dupes() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            await seed_user(s, role)
            await seed_account(s, account_id=1, team_id=None)
            same = dt(2026, 6, 1, 12, 0)
            # 7 писем с ОДНОЙ И ТОЙ ЖЕ секундой, разные id (BIGSERIAL).
            for uid in range(1, 8):
                await seed_message(s, account_id=1, uid=uid, internal_date=same)
            # Плюс одно письмо позже и одно раньше — границы страниц.
            await seed_message(s, account_id=1, uid=100, internal_date=dt(2026, 6, 2))
            await seed_message(s, account_id=1, uid=101, internal_date=dt(2026, 5, 31))
            await s.commit()
        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            ids = await _collect_all_pages(c, {"limit": 2})
    # 9 писем: без пропусков и без дублей несмотря на совпадающие даты (limit=2 бьёт
    # границы страниц ровно внутри блока одинаковой секунды — односоставный курсор
    # здесь потерял/задублировал бы строки).
    assert len(ids) == 9
    assert len(set(ids)) == 9
    # Порядок по (internal_date DESC, id DESC): новейшая дата (id 8) → блок одинаковой
    # секунды ids 7..1 по убыванию → старейшая дата (id 9). Непрерывность блока 7..1
    # доказывает компаундность курсора.
    assert ids == [8, 7, 6, 5, 4, 3, 2, 1, 9]


async def test_keyset_full_scan_matches_unpaged() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            await seed_user(s, role)
            await seed_account(s, account_id=1, team_id=None)
            same = dt(2026, 6, 1, 12, 0)
            for uid in range(1, 11):
                await seed_message(s, account_id=1, uid=uid, internal_date=same)
            await s.commit()
        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            paged = await _collect_all_pages(c, {"limit": 3})
            full = await c.get("/api/mail/messages", params={"limit": 200})
    unpaged = [m["id"] for m in full.json()["messages"]]
    assert paged == unpaged  # порядок и состав совпадают


# --- Невалидный курсор / лимит ----------------------------------------------
async def test_invalid_cursor_400() -> None:
    async with mail_db() as sm:
        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get("/api/mail/messages", params={"before": "!!!broken!!!"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize("limit", [0, 201, -5])
async def test_invalid_limit_400(limit: int) -> None:
    async with mail_db() as sm:
        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get("/api/mail/messages", params={"limit": limit})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_limit"


# --- Анти-энумерация: пустой scope → пусто, БЕЗ запроса ленты и агрегатора ----
async def test_empty_scope_returns_empty_without_feed_query_or_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role)  # НЕ состоит ни в одной команде
            other_team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=other_team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            user_id = user.id

            await s.commit()
        # Если сервис попытается выбрать ленту при пустом scope — тест упадёт.
        from app.repositories.mail_message_repository import MailMessageRepository

        async def _boom(*_a: Any, **_k: Any) -> list[Any]:
            raise AssertionError("list_feed НЕ должен вызываться при пустом scope")

        monkeypatch.setattr(MailMessageRepository, "list_feed", _boom)

        fake = FakeMailClient()
        principal = build_principal(
            user_id=user_id, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal, mail_client=fake)
        async with client(app) as c:
            resp = await c.get("/api/mail/messages", params={"limit": 50})
    assert resp.status_code == 200
    assert resp.json() == {"messages": [], "next_cursor": None}
    assert fake.calls == []  # внешний клиент не тронут


# --- Чужой фильтр (mail_account_id/team_id) → пусто, НЕ 403 -------------------
async def test_foreign_account_filter_returns_empty_not_403() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await seed_account(s, account_id=1, team_id=my_team.id)
            await seed_account(s, account_id=2, team_id=other_team.id)
            await seed_message(s, account_id=2, uid=1, internal_date=dt())
            user_id = user.id
            await s.commit()
        principal = build_principal(
            user_id=user_id, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            # Фильтрует по чужому ящику 2 → пересечение со scope пусто → пустая страница.
            resp = await c.get("/api/mail/messages", params={"mail_account_id": 2})
    assert resp.status_code == 200
    assert resp.json()["messages"] == []


async def test_foreign_team_filter_returns_empty() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await seed_account(s, account_id=2, team_id=other_team.id)
            await seed_message(s, account_id=2, uid=1, internal_date=dt())
            user_id = user.id
            other_id = other_team.id
            await s.commit()
        principal = build_principal(
            user_id=user_id, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get("/api/mail/messages", params={"team_id": str(other_id)})
    assert resp.status_code == 200
    assert resp.json()["messages"] == []


# --- Scope списка ящиков -----------------------------------------------------
async def test_list_mailboxes_scoped_to_member_teams() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await seed_account(s, account_id=1, team_id=my_team.id)
            await seed_account(s, account_id=2, team_id=other_team.id)
            await seed_account(s, account_id=3, team_id=None)  # unassigned
            user_id = user.id
            await s.commit()
        principal = build_principal(
            user_id=user_id, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get("/api/mail/mailboxes")
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["mailboxes"]}
    assert ids == {1}  # только ящик своей команды; чужой и unassigned скрыты


async def test_list_mailboxes_admin_sees_all() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_account(s, account_id=2, team_id=None)
            await s.commit()
        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get("/api/mail/mailboxes")
    assert {m["id"] for m in resp.json()["mailboxes"]} == {1, 2}
