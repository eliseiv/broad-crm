"""Integration/contract ADR-048: `mailbox_count` в /api/teams + строка `TeamMailboxItem`.

Реальный Postgres + FastAPI-app (`mail_s34_helpers.build_app` — переопределены только
сессия/принципал/`MailClient`, `TeamService` и репозитории настоящие). Проверяется:
счётчик почт во ВСЕХ трёх телах (`GET` список, `201 POST`, `200 PATCH`), `0` у команды
без ящиков, пересчёт после переноса ящика в другую команду, состав полей
`TeamMailboxItem` (есть `number`/`app_name`, в т.ч. `null`; нет кредов/хостов/полей
синка), неизменившийся гейт `teams:view` (403) и отсутствие N+1 на батч-агрегате.

Нормативные значения — `docs/04-api.md` §Teams (схемы `TeamListItem`/`TeamMailboxItem`)
и `docs/adr/ADR-048-teams-mailbox-count-mail-row.md`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.models.mail_account import MailAccount
from mail_s34_helpers import (
    FakeMailClient,
    build_app,
    build_principal,
    client,
    mail_db,
    seed_account,
    seed_team,
)
from sqlalchemy import event, update
from sqlalchemy.engine import Engine

# Нормативный состав полей элемента списка почт команды (04-api.md §TeamMailboxItem).
_TEAM_MAILBOX_ITEM_KEYS = {"id", "email", "number", "app_name", "display_name", "is_active"}

# Поля, которые эндпоинт НЕ раскрывает держателю `teams:view` (сужение ADR-044 §4,
# сохранено ADR-048 §2): креды/хосты и статус синка.
_FORBIDDEN_MAILBOX_KEYS = {
    "imap_host",
    "imap_port",
    "imap_username",
    "imap_password",
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "password",
    "last_synced_at",
    "last_sync_error",
    "consecutive_failures",
}


def _superadmin_app(sm: Any) -> Any:
    return build_app(sm, build_principal(is_superadmin=True), mail_client=FakeMailClient())


# --- mailbox_count: список ---------------------------------------------------


async def test_list_teams_mailbox_count_zero_for_team_without_mailboxes() -> None:
    """Команда без ящиков → ключ `mailbox_count` присутствует и равен `0` (ADR-048 §1)."""
    async with mail_db() as sm:
        async with sm() as s:
            await seed_team(s, name="pustaya")
            await s.commit()
        async with client(_superadmin_app(sm)) as c:
            resp = await c.get("/api/teams")

    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert "mailbox_count" in item
    assert item["mailbox_count"] == 0


async def test_list_teams_mailbox_count_counts_own_mailboxes_only() -> None:
    """Счётчик = COUNT(mail_accounts WHERE team_id = teams.id); чужие/unassigned не в счёт."""
    async with mail_db() as sm:
        async with sm() as s:
            sales = await seed_team(s, name="prodazhi")
            support = await seed_team(s, name="podderzhka")
            await seed_account(s, account_id=1, team_id=sales.id)
            await seed_account(s, account_id=2, team_id=sales.id)
            await seed_account(s, account_id=3, team_id=support.id)
            await seed_account(s, account_id=4, team_id=None)  # unassigned
            await s.commit()
        async with client(_superadmin_app(sm)) as c:
            resp = await c.get("/api/teams")

    counts = {t["name"]: t["mailbox_count"] for t in resp.json()["items"]}
    assert counts == {"prodazhi": 2, "podderzhka": 1}


# --- mailbox_count: тела 201 / 200 -------------------------------------------


async def test_mailbox_count_present_in_create_and_patch_bodies() -> None:
    """`mailbox_count` есть во всех трёх телах: 201 POST, 200 PATCH, элемент списка."""
    async with mail_db() as sm, client(_superadmin_app(sm)) as c:
        created = await c.post("/api/teams", json={"name": "prodazhi"})
        team_id = created.json()["id"]
        # Ящик привязан к уже существующей команде → 200 PATCH обязан вернуть 1.
        async with sm() as s:
            await seed_account(s, account_id=1, team_id=uuid.UUID(team_id))
            await s.commit()
        patched = await c.patch(f"/api/teams/{team_id}", json={"name": "prodazhi-eu"})
        listed = await c.get("/api/teams")

    assert created.status_code == 201, created.text
    # У только что созданной команды ящиков нет → 0 (не null, не пропуск ключа).
    assert created.json()["mailbox_count"] == 0
    assert patched.status_code == 200, patched.text
    assert patched.json()["mailbox_count"] == 1
    assert listed.json()["items"][0]["mailbox_count"] == 1


async def test_mailbox_count_recomputed_after_mailbox_transferred_to_other_team() -> None:
    """Перенос ящика в другую команду → оба счётчика пересчитаны (агрегат на лету, не колонка)."""
    async with mail_db() as sm:
        async with sm() as s:
            sales = await seed_team(s, name="prodazhi")
            support = await seed_team(s, name="podderzhka")
            await seed_account(s, account_id=1, team_id=sales.id)
            await s.commit()
        async with client(_superadmin_app(sm)) as c:
            before = await c.get("/api/teams")
            async with sm() as s:
                await s.execute(
                    update(MailAccount).where(MailAccount.id == 1).values(team_id=support.id)
                )
                await s.commit()
            after = await c.get("/api/teams")

    assert {t["name"]: t["mailbox_count"] for t in before.json()["items"]} == {
        "prodazhi": 1,
        "podderzhka": 0,
    }
    assert {t["name"]: t["mailbox_count"] for t in after.json()["items"]} == {
        "prodazhi": 0,
        "podderzhka": 1,
    }


async def test_list_teams_mailbox_count_is_batch_aggregate_without_n_plus_1() -> None:
    """Батч-агрегат: один GROUP BY по mail_accounts на весь список, а не запрос на команду."""
    statements: list[str] = []

    def _record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    async with mail_db() as sm:
        async with sm() as s:
            for idx in range(1, 4):
                team = await seed_team(s, name=f"komanda{idx}")
                await seed_account(s, account_id=idx, team_id=team.id)
            await s.commit()
        async with client(_superadmin_app(sm)) as c:
            event.listen(Engine, "before_cursor_execute", _record)
            try:
                resp = await c.get("/api/teams")
            finally:
                event.remove(Engine, "before_cursor_execute", _record)

    assert resp.status_code == 200, resp.text
    assert sorted(t["mailbox_count"] for t in resp.json()["items"]) == [1, 1, 1]
    # 3 команды, но обращений к mail_accounts ровно ОДНО (батч `count_by_teams`).
    mail_account_queries = [s for s in statements if "mail_accounts" in s]
    assert len(mail_account_queries) == 1, mail_account_queries
    assert "GROUP BY" in mail_account_queries[0].upper()


# --- TeamMailboxItem: состав полей (04-api.md §TeamMailboxItem) ---------------


async def test_team_mailboxes_item_exposes_number_and_app_name() -> None:
    """Элемент списка почт команды содержит «Номер»/«Приложение» (ADR-048 §2)."""
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="prodazhi")
            await seed_account(
                s,
                account_id=1,
                email="inbox@example.com",
                number="5108",
                app_name="Klyro Forge (Codex)",
                display_name="5108 Klyro Forge (Codex)",
                team_id=team.id,
                is_active=True,
            )
            await s.commit()
        async with client(_superadmin_app(sm)) as c:
            resp = await c.get(f"/api/teams/{team.id}/mailboxes")

    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["mailboxes"]
    assert item["number"] == "5108"
    assert item["app_name"] == "Klyro Forge (Codex)"
    assert item["email"] == "inbox@example.com"
    assert item["is_active"] is True
    assert set(item) == _TEAM_MAILBOX_ITEM_KEYS


async def test_team_mailboxes_item_number_and_app_name_may_be_null() -> None:
    """`number`/`app_name` не заданы → `null` (ключи присутствуют, схема не сужается)."""
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="prodazhi")
            await seed_account(s, account_id=1, team_id=team.id, is_active=False)
            await s.commit()
        async with client(_superadmin_app(sm)) as c:
            resp = await c.get(f"/api/teams/{team.id}/mailboxes")

    (item,) = resp.json()["mailboxes"]
    assert item["number"] is None
    assert item["app_name"] is None
    assert item["display_name"] is None
    assert item["is_active"] is False
    assert set(item) == _TEAM_MAILBOX_ITEM_KEYS


async def test_team_mailboxes_item_hides_credentials_hosts_and_sync_fields() -> None:
    """Держателю `teams:view` НЕ раскрываются креды/хосты и статус синка (ADR-044 §4/ADR-048 §2)."""
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="prodazhi")
            await seed_account(
                s,
                account_id=1,
                team_id=team.id,
                number="5108",
                app_name="Klyro Forge (Codex)",
                last_sync_error="IMAP auth failed",
            )
            await s.commit()
        async with client(_superadmin_app(sm)) as c:
            resp = await c.get(f"/api/teams/{team.id}/mailboxes")

    (item,) = resp.json()["mailboxes"]
    assert set(item) & _FORBIDDEN_MAILBOX_KEYS == set()
    assert "IMAP auth failed" not in resp.text


# --- Гейт видимости (ADR-048 §4: не изменился — `teams:view`) -----------------


@pytest.mark.parametrize("path", ["/api/teams", "/api/teams/{team_id}/mailboxes"])
async def test_gate_unchanged_teams_view_required(path: str) -> None:
    """Гейт остался `teams:view`: без права — 403 forbidden и на списке, и на почтах команды."""
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="prodazhi")
            await seed_account(s, account_id=1, team_id=team.id)
            await s.commit()
        no_teams = build_principal(
            is_superadmin=False, role="operator", permissions={"mail": ["view"]}
        )
        app = build_app(sm, no_teams, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get(path.format(team_id=team.id))

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_gate_teams_view_alone_grants_mailbox_count_and_mail_row() -> None:
    """`teams:view` без прав `mail:*` достаточно для счётчика и списка почт (ADR-048 §4)."""
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="prodazhi")
            await seed_account(s, account_id=1, team_id=team.id, number="5108")
            await s.commit()
        only_teams_view = build_principal(
            is_superadmin=False, role="operator", permissions={"teams": ["view"]}
        )
        app = build_app(sm, only_teams_view, mail_client=FakeMailClient())
        async with client(app) as c:
            listed = await c.get("/api/teams")
            mailboxes = await c.get(f"/api/teams/{team.id}/mailboxes")

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["mailbox_count"] == 1
    assert mailboxes.status_code == 200, mailboxes.text
    assert mailboxes.json()["mailboxes"][0]["number"] == "5108"


async def test_team_mailboxes_unknown_team_is_404_team_not_found() -> None:
    """Несуществующая команда → 404 `team_not_found` (поведение ADR-044 §4 не менялось)."""
    async with mail_db() as sm, client(_superadmin_app(sm)) as c:
        resp = await c.get(f"/api/teams/{uuid.uuid4()}/mailboxes")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "team_not_found"
