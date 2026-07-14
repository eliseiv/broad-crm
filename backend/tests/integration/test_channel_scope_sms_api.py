"""Per-channel scope СМС + перенос номера (ADR-055 §3/§3.2/§5.3) — реальный Postgres.

Перечень нормативен (06-testing-strategy.md §«Per-channel scope команд»); канал `sms`
покрывается ОТДЕЛЬНО от почты («работает почта» за СМС не засчитывается). Поэлементно:

- **union-семантика** — доп-команда даёт и ленту, и каталог номеров, и мутацию;
- **«Без команды» (`sms_includes_unassigned`)** — бесхозный номер виден и мутируется
  (правка/удаление/перенос); без флага — в чтении отсутствует (НЕ 403), мутация → 403;
- **`no_team`** — только бесхозные; `team_id` + `no_team=true` → 400 validation_error;
  без флага → пустая страница (не 403);
- **перенос номера (§3.2) — ПО КЕЙСУ НА КАЖДУЮ проверку (а)…(ж)**, включая
  анти-энумерацию: не-админу НЕсуществующая целевая команда даёт **403**, а НЕ 404
  (`404 sms_team_not_found` остаётся ответом admin-уровня).
"""

from __future__ import annotations

import uuid
from typing import Any

from sms_helpers import (
    add_extra_team,
    add_membership,
    build_app,
    build_principal,
    client,
    seed_inbound,
    seed_number,
    seed_role,
    seed_team,
    seed_user,
    sms_db,
)

_SMS_FULL = {"sms": ["view", "edit", "delete", "transfer", "sync"]}


def _non_admin(user_id: uuid.UUID, **flags: bool) -> Any:
    """Принципал не-админа с ПОЛНЫМИ правами СМС (но не полным каталогом) — §3."""
    return build_principal(
        user_id=user_id,
        is_superadmin=False,
        role="Оператор",
        permissions=_SMS_FULL,
        **flags,
    )


# --- union-семантика ---------------------------------------------------------


async def test_union_scope_feed_and_catalog_include_base_and_extra_team() -> None:
    """Не-админ с доп-командой B видит номера и SMS ОБЕИХ команд (A ∪ B), §3."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            team_c = await seed_team(s)  # чужая
            await add_membership(s, user.id, team_a.id)
            await add_extra_team(s, user.id, "sms", team_b.id)
            await seed_number(s, phone_number="+15550000001", team_id=team_a.id)
            await seed_number(s, phone_number="+15550000002", team_id=team_b.id)
            await seed_number(s, phone_number="+15550000003", team_id=team_c.id)
            for i in (1, 2, 3):
                await seed_inbound(s, from_number="+15551110000", to_number=f"+1555000000{i}")
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            numbers = await c.get("/api/sms/numbers")
            feed = await c.get("/api/sms/messages")

    assert {n["phone_number"] for n in numbers.json()["numbers"]} == {
        "+15550000001",
        "+15550000002",
    }
    assert {m["to_number"] for m in feed.json()["messages"]} == {
        "+15550000001",
        "+15550000002",
    }


async def test_extra_team_number_is_mutable_by_non_admin() -> None:
    """Доп-команда — полноценная команда канала (§4): номер команды B правится (200)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            await add_extra_team(s, user.id, "sms", team_b.id)
            number = await seed_number(s, phone_number="+15550000002", team_id=team_b.id)
            await s.commit()
            user_id, number_id = user.id, number.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.patch(f"/api/sms/numbers/{number_id}", json={"note": "новая"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] == "новая"


async def test_mail_extra_team_does_not_leak_into_sms_scope() -> None:
    """Каналы независимы: доп-команда канала `mail` НЕ расширяет scope СМС (§1)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            await add_extra_team(s, user.id, "mail", team_b.id)  # ДРУГОЙ канал
            number = await seed_number(s, phone_number="+15550000002", team_id=team_b.id)
            await s.commit()
            user_id, number_id = user.id, number.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            numbers = await c.get("/api/sms/numbers")
            mutation = await c.patch(f"/api/sms/numbers/{number_id}", json={"note": "x"})

    assert numbers.json()["numbers"] == []
    assert mutation.status_code == 403
    assert mutation.json()["error"]["code"] == "forbidden"


# --- Флаг «Без команды» (sms_includes_unassigned) -----------------------------


async def test_includes_unassigned_orphan_number_visible_and_mutable() -> None:
    """С флагом: бесхозный номер виден в каталоге/ленте и МУТИРУЕТСЯ, включая удаление (§3.1)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            orphan = await seed_number(s, phone_number="+15550000009", team_id=None)
            await seed_inbound(s, from_number="+15551110000", to_number="+15550000009")
            await s.commit()
            user_id, number_id = user.id, orphan.id

        app = build_app(sm, _non_admin(user_id, sms_includes_unassigned=True))
        async with client(app) as c:
            numbers = await c.get("/api/sms/numbers")
            feed = await c.get("/api/sms/messages")
            patched = await c.patch(f"/api/sms/numbers/{number_id}", json={"note": "n"})
            deleted = await c.delete(f"/api/sms/numbers/{number_id}")

    assert [n["phone_number"] for n in numbers.json()["numbers"]] == ["+15550000009"]
    assert [m["to_number"] for m in feed.json()["messages"]] == ["+15550000009"]
    assert patched.status_code == 200, patched.text
    assert deleted.status_code == 204, deleted.text


async def test_without_flag_orphan_number_absent_in_read_and_403_on_mutation() -> None:
    """Без флага: бесхозный номер в чтении ОТСУТСТВУЕТ (не 403), мутация → 403 (§3)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await seed_number(s, phone_number="+15550000001", team_id=team_a.id)
            orphan = await seed_number(s, phone_number="+15550000009", team_id=None)
            await seed_inbound(s, from_number="+15551110000", to_number="+15550000009")
            await s.commit()
            user_id, number_id = user.id, orphan.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            numbers = await c.get("/api/sms/numbers")
            feed = await c.get("/api/sms/messages")
            patched = await c.patch(f"/api/sms/numbers/{number_id}", json={"note": "n"})
            deleted = await c.delete(f"/api/sms/numbers/{number_id}")

    assert numbers.status_code == 200
    assert [n["phone_number"] for n in numbers.json()["numbers"]] == ["+15550000001"]
    assert feed.json()["messages"] == []  # анти-энумерация: пусто, НЕ 403
    assert patched.status_code == 403
    assert patched.json()["error"]["code"] == "forbidden"
    assert deleted.status_code == 403


# --- Фильтр `no_team` ленты (§5.3) -------------------------------------------


async def test_no_team_filter_returns_only_orphan_number_messages() -> None:
    """`no_team=true` → ТОЛЬКО SMS номеров без команды (§5.3)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await seed_number(s, phone_number="+15550000001", team_id=team_a.id)
            await seed_number(s, phone_number="+15550000009", team_id=None)
            await seed_inbound(s, from_number="+15551110000", to_number="+15550000001")
            await seed_inbound(s, from_number="+15551110000", to_number="+15550000009")
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id, sms_includes_unassigned=True))
        async with client(app) as c:
            resp = await c.get("/api/sms/messages", params={"no_team": "true"})

    assert resp.status_code == 200, resp.text
    assert [m["to_number"] for m in resp.json()["messages"]] == ["+15550000009"]


async def test_no_team_with_team_id_is_400_validation_error() -> None:
    """`team_id` и `no_team=true` взаимоисключающи → 400 validation_error (§5.3)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await s.commit()
            user_id, team_id = user.id, team_a.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.get(
                "/api/sms/messages",
                params={"team_id": str(team_id), "no_team": "true"},
            )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


async def test_no_team_without_flag_returns_empty_page_not_403() -> None:
    """`no_team=true` у не-админа БЕЗ флага → пустая страница (анти-энумерация, не 403)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await seed_number(s, phone_number="+15550000009", team_id=None)
            await seed_inbound(s, from_number="+15551110000", to_number="+15550000009")
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.get("/api/sms/messages", params={"no_team": "true"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["messages"] == []


# --- Перенос номера (§3.2): по кейсу на КАЖДУЮ проверку (а)…(ж) ---------------


async def test_transfer_a_number_out_of_scope_is_403() -> None:
    """(а) Сам номер вне scope → 403 (проверка №1, `_require_mutation_scope`)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            mine = await seed_team(s)
            foreign = await seed_team(s)
            await add_membership(s, user.id, mine.id)
            number = await seed_number(s, phone_number="+15550000003", team_id=foreign.id)
            await s.commit()
            user_id, number_id, target = user.id, number.id, mine.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer", json={"team_id": str(target)}
            )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


async def test_transfer_b_null_team_without_flag_is_403() -> None:
    """(б) Не-админ, `team_id=null`, БЕЗ `sms_includes_unassigned` → 403 (§3.2 п.2).

    Иначе актор безвозвратно выбросил бы номер из собственного scope (самоблокировка,
    прежний TD-060).
    """
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            number = await seed_number(s, phone_number="+15550000001", team_id=team_a.id)
            await s.commit()
            user_id, number_id = user.id, number.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.post(f"/api/sms/numbers/{number_id}/transfer", json={"team_id": None})

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


async def test_transfer_c_null_team_with_flag_is_200_and_number_stays_visible() -> None:
    """(в) Не-админ, `team_id=null`, С флагом → 200; номер остаётся видимым (операция обратима)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            number = await seed_number(s, phone_number="+15550000001", team_id=team_a.id)
            await s.commit()
            user_id, number_id = user.id, number.id

        app = build_app(sm, _non_admin(user_id, sms_includes_unassigned=True))
        async with client(app) as c:
            resp = await c.post(f"/api/sms/numbers/{number_id}/transfer", json={"team_id": None})
            after = await c.get("/api/sms/numbers")

    assert resp.status_code == 200, resp.text
    assert resp.json()["team"] is None
    # Номер стал бесхозным, но флаг оставляет его видимым ⇒ перенос обратим.
    assert [n["id"] for n in after.json()["numbers"]] == [number_id]


async def test_transfer_d_foreign_existing_team_is_403() -> None:
    """(г) Не-админ, целевая команда СУЩЕСТВУЕТ, но вне scope → 403 (§3.2 п.3)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            mine = await seed_team(s)
            foreign = await seed_team(s)
            await add_membership(s, user.id, mine.id)
            number = await seed_number(s, phone_number="+15550000001", team_id=mine.id)
            await s.commit()
            user_id, number_id, target = user.id, number.id, foreign.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer", json={"team_id": str(target)}
            )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


async def test_transfer_e_nonexistent_team_is_403_for_non_admin_not_404() -> None:
    """(д) Не-админ, целевая команда НЕ СУЩЕСТВУЕТ → **403**, а НЕ 404 (анти-энумерация).

    Проверка scope идёт ПЕРВОЙ (§3.2 п.3): не-админ не должен различать «команды нет» и
    «команда чужая». `404 sms_team_not_found` — ответ admin-уровня (см. кейс (е)).
    """
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            mine = await seed_team(s)
            await add_membership(s, user.id, mine.id)
            number = await seed_number(s, phone_number="+15550000001", team_id=mine.id)
            await s.commit()
            user_id, number_id = user.id, number.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer",
                json={"team_id": str(uuid.uuid4())},  # заведомо не существует
            )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"
    assert resp.json()["error"]["code"] != "sms_team_not_found"


async def test_transfer_f_nonexistent_team_is_404_for_admin_level() -> None:
    """(е) Актор admin-уровня, команда не существует → 404 sms_team_not_found (§3.2 п.3)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            number = await seed_number(s, phone_number="+15550000001", team_id=team_a.id)
            await s.commit()
            user_id, number_id = user.id, number.id

        # БД-пользователь с ПОЛНЫМ каталогом прав → admin-уровень (не супер-админ).
        app = build_app(sm, build_principal(user_id=user_id, is_superadmin=False, role="Админ"))
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer",
                json={"team_id": str(uuid.uuid4())},
            )

    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "sms_team_not_found"


async def test_transfer_g_into_own_extra_team_is_200() -> None:
    """(ж) Не-админ, целевая команда — его ДОП-команда → 200.

    §3.2 п.3: `team_ids` = базовые ∪ добавка ⇒ доп-команда — легальная цель переноса.
    """
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await add_extra_team(s, user.id, "sms", team_b.id)
            number = await seed_number(s, phone_number="+15550000001", team_id=team_a.id)
            await s.commit()
            user_id, number_id, target = user.id, number.id, team_b.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer", json={"team_id": str(target)}
            )

    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["id"] == str(target)


async def test_transfer_orphan_number_into_own_team_with_flag_is_200() -> None:
    """Носитель флага переносит БЕСХОЗНЫЙ номер в свою команду → 200 (§3.1: полные права)."""
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            orphan = await seed_number(s, phone_number="+15550000009", team_id=None)
            await s.commit()
            user_id, number_id, target = user.id, orphan.id, team_a.id

        app = build_app(sm, _non_admin(user_id, sms_includes_unassigned=True))
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer", json={"team_id": str(target)}
            )

    assert resp.status_code == 200, resp.text
    assert resp.json()["team"]["id"] == str(target)
