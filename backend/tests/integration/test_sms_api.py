"""Контрактные/scope integration-тесты приватного SMS API (04-api.md#sms, ADR-030).

Реальный Postgres (sms_helpers.sms_db). Покрывают: список номеров + scope видимости
(супер-админ/не-админ/unassigned/анти-энумерация), PATCH presence-семантику,
transfer (null/чужой team → 404), delete (204 + история SMS), ленту сообщений
(keyset-курсор, комбинируемые фильтры, invalid_cursor/invalid_limit), teams
`number_count` + GET /api/teams/{id}/numbers. Имена полей/коды — по 04-api.md.
"""

from __future__ import annotations

import uuid

from sms_helpers import (
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

_SMS_VIEW = {"sms": ["view", "edit", "transfer", "sync", "delete"]}


# --- GET /api/sms/numbers + scope -------------------------------------------


async def test_numbers_superadmin_sees_all_including_unassigned() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_number(
                s,
                phone_number="+13105550001",
                team_id=team.id,
                label="Sales",
                login="acme",
                app_name="WhatsApp",
                note="резерв",
            )
            await seed_number(s, phone_number="+13105550002", team_id=None)
            await s.commit()
        app = build_app(sm, build_principal())  # супер-админ
        async with client(app) as c:
            resp = await c.get("/api/sms/numbers")

    assert resp.status_code == 200
    numbers = resp.json()["numbers"]
    assert {n["phone_number"] for n in numbers} == {"+13105550001", "+13105550002"}
    assigned = next(n for n in numbers if n["phone_number"] == "+13105550001")
    # GET /api/sms/numbers отдаёт ПОЛНЫЙ SmsNumberItem (контракт sms:* не сужался).
    assert assigned["label"] == "Sales"
    assert assigned["login"] == "acme"
    assert assigned["app_name"] == "WhatsApp"
    assert assigned["note"] == "резерв"
    assert assigned["is_active"] is True
    assert "created_at" in assigned and "updated_at" in assigned
    assert assigned["team"]["name"] == team.name
    unassigned = next(n for n in numbers if n["phone_number"] == "+13105550002")
    assert unassigned["team"] is None


async def test_numbers_non_admin_sees_only_own_team() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_VIEW)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await seed_number(s, phone_number="+13105550010", team_id=my_team.id)
            await seed_number(s, phone_number="+13105550011", team_id=other_team.id)
            await seed_number(s, phone_number="+13105550012", team_id=None)
            user_id = user.id
            await s.commit()
        principal = build_principal(user_id=user_id, is_superadmin=False, permissions=_SMS_VIEW)
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/sms/numbers")

    assert resp.status_code == 200
    phones = {n["phone_number"] for n in resp.json()["numbers"]}
    assert phones == {"+13105550010"}  # чужая команда и unassigned невидимы


async def test_numbers_non_admin_without_teams_is_empty() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_VIEW)
            user = await seed_user(s, role)
            team = await seed_team(s)
            await seed_number(s, phone_number="+13105550020", team_id=team.id)
            user_id = user.id
            await s.commit()
        principal = build_principal(user_id=user_id, is_superadmin=False, permissions=_SMS_VIEW)
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/sms/numbers")

    assert resp.status_code == 200
    assert resp.json()["numbers"] == []


async def test_numbers_gate_forbidden_without_view() -> None:
    async with sms_db() as sm:
        principal = build_principal(is_superadmin=False, permissions={"servers": ["view"]})
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/sms/numbers")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# --- PATCH /api/sms/numbers/{id} (presence-семантика) -----------------------


async def test_patch_sets_stripped_value() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            number = await seed_number(s, phone_number="+13105550100", team_id=team.id)
            number_id = number.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.patch(
                f"/api/sms/numbers/{number_id}", json={"login": "  acme  ", "app_name": "WhatsApp"}
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["login"] == "acme"  # strip
    assert body["app_name"] == "WhatsApp"
    assert body["note"] is None


async def test_patch_clears_field_with_empty_string() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            number = await seed_number(s, phone_number="+13105550101", note="старое")
            number_id = number.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.patch(f"/api/sms/numbers/{number_id}", json={"note": ""})
    assert resp.status_code == 200
    assert resp.json()["note"] is None


async def test_patch_omitted_field_unchanged() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            number = await seed_number(
                s, phone_number="+13105550102", login="keep", note="keepnote"
            )
            number_id = number.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.patch(f"/api/sms/numbers/{number_id}", json={"login": "changed"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["login"] == "changed"
    assert body["note"] == "keepnote"  # не передан → не изменён


async def test_patch_over_max_length_is_400_validation_error() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            number = await seed_number(s, phone_number="+13105550103")
            number_id = number.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.patch(f"/api/sms/numbers/{number_id}", json={"login": "x" * 201})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


async def test_patch_missing_number_is_404() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.patch("/api/sms/numbers/999999", json={"login": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "sms_number_not_found"


async def test_patch_non_admin_unassigned_is_403_not_404_leak() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_VIEW)
            user = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, user.id, team.id)
            unassigned = await seed_number(s, phone_number="+13105550104", team_id=None)
            unassigned_id = unassigned.id
            user_id = user.id
            await s.commit()
        principal = build_principal(user_id=user_id, is_superadmin=False, permissions=_SMS_VIEW)
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.patch(f"/api/sms/numbers/{unassigned_id}", json={"login": "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# --- POST /api/sms/numbers/{id}/transfer ------------------------------------


async def test_transfer_assigns_team() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="Продажи")
            number = await seed_number(s, phone_number="+13105550200", team_id=None)
            number_id, team_id = number.id, team.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer", json={"team_id": str(team_id)}
            )
    assert resp.status_code == 200
    assert resp.json()["team"]["id"] == str(team_id)


async def test_transfer_null_unassigns() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            number = await seed_number(s, phone_number="+13105550201", team_id=team.id)
            number_id = number.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(f"/api/sms/numbers/{number_id}/transfer", json={"team_id": None})
    assert resp.status_code == 200
    assert resp.json()["team"] is None


async def test_transfer_nonexistent_team_is_404() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            number = await seed_number(s, phone_number="+13105550202")
            number_id = number.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                f"/api/sms/numbers/{number_id}/transfer", json={"team_id": str(uuid.uuid4())}
            )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "sms_team_not_found"


# --- DELETE /api/sms/numbers/{id} -------------------------------------------


async def test_delete_number_204_and_history_preserved() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            number = await seed_number(s, phone_number="+13105550300", team_id=team.id)
            await seed_inbound(
                s, from_number="+79161234567", to_number="+13105550300", team_id=team.id
            )
            number_id = number.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            deleted = await c.delete(f"/api/sms/numbers/{number_id}")
            repeat = await c.delete(f"/api/sms/numbers/{number_id}")
            messages = await c.get("/api/sms/messages")

    assert deleted.status_code == 204
    assert repeat.status_code == 404
    assert repeat.json()["error"]["code"] == "sms_number_not_found"
    # История SMS сохраняется; номер удалён → number=null (виден супер-админу).
    items = messages.json()["messages"]
    assert len(items) == 1
    assert items[0]["to_number"] == "+13105550300"
    assert items[0]["number"] is None


# --- GET /api/sms/messages (keyset + фильтры + scope) ------------------------


async def test_messages_keyset_pagination() -> None:
    from datetime import UTC, datetime

    async with sms_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_number(s, phone_number="+13105550400", team_id=team.id)
            for i in range(5):
                await seed_inbound(
                    s,
                    from_number="+79160000000",
                    to_number="+13105550400",
                    body=f"msg{i}",
                    team_id=team.id,
                    received_at=datetime(2026, 7, 9, 12, i, tzinfo=UTC),
                )
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            page1 = await c.get("/api/sms/messages", params={"limit": 2})
            cursor = page1.json()["next_cursor"]
            page2 = await c.get("/api/sms/messages", params={"limit": 2, "cursor": cursor})

    assert page1.status_code == 200
    ids1 = [m["id"] for m in page1.json()["messages"]]
    assert len(ids1) == 2
    assert ids1 == sorted(ids1, reverse=True)  # newest-first
    assert cursor is not None
    ids2 = [m["id"] for m in page2.json()["messages"]]
    assert set(ids1).isdisjoint(ids2)  # без пересечения страниц
    assert max(ids2) < min(ids1)  # страница 2 — старее


async def test_messages_combined_filters_are_and() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            num_a = await seed_number(s, phone_number="+13105550500", team_id=team_a.id)
            await seed_number(s, phone_number="+13105550501", team_id=team_b.id)
            await seed_inbound(
                s, from_number="+79160000001", to_number="+13105550500", team_id=team_a.id
            )
            await seed_inbound(
                s, from_number="+79160000002", to_number="+13105550501", team_id=team_b.id
            )
            num_a_id, team_a_id, team_b_id = num_a.id, team_a.id, team_b.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            # number_id принадлежит team_a; фильтр team_b → пересечение пусто.
            mismatch = await c.get(
                "/api/sms/messages", params={"number_id": num_a_id, "team_id": str(team_b_id)}
            )
            match = await c.get(
                "/api/sms/messages", params={"number_id": num_a_id, "team_id": str(team_a_id)}
            )
    assert mismatch.json()["messages"] == []
    assert [m["to_number"] for m in match.json()["messages"]] == ["+13105550500"]


async def test_messages_filter_out_of_scope_is_empty_page() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_SMS_VIEW)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            other_num = await seed_number(s, phone_number="+13105550600", team_id=other_team.id)
            await seed_inbound(
                s, from_number="+79160000003", to_number="+13105550600", team_id=other_team.id
            )
            other_num_id, user_id = other_num.id, user.id
            await s.commit()
        principal = build_principal(user_id=user_id, is_superadmin=False, permissions=_SMS_VIEW)
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/sms/messages", params={"number_id": other_num_id})
    assert resp.status_code == 200
    assert resp.json()["messages"] == []  # анти-энумерация: пусто, не 403/404


async def test_messages_invalid_cursor_is_400() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get("/api/sms/messages", params={"cursor": "!!!broken!!!"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_cursor"


async def test_messages_invalid_limit_is_400() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            too_big = await c.get("/api/sms/messages", params={"limit": 200})
            too_small = await c.get("/api/sms/messages", params={"limit": 0})
    assert too_big.status_code == 400
    assert too_big.json()["error"]["code"] == "invalid_limit"
    assert too_small.status_code == 400
    assert too_small.json()["error"]["code"] == "invalid_limit"


# --- teams: number_count + GET /api/teams/{id}/numbers ----------------------


async def test_teams_number_count_batch() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            team_a = await seed_team(s, name="A")
            team_b = await seed_team(s, name="B")
            await seed_number(s, phone_number="+13105550700", team_id=team_a.id)
            await seed_number(s, phone_number="+13105550701", team_id=team_a.id)
            await seed_number(s, phone_number="+13105550702", team_id=team_b.id)
            team_a_id, team_b_id = team_a.id, team_b.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get("/api/teams")
    assert resp.status_code == 200
    counts = {item["id"]: item["number_count"] for item in resp.json()["items"]}
    assert counts[str(team_a_id)] == 2
    assert counts[str(team_b_id)] == 1


async def test_team_numbers_endpoint_minimal_schema_and_404() -> None:
    # ADR-030 §8: GET /api/teams/{id}/numbers под гейтом teams:view отдаёт МИНИМАЛЬНУЮ
    # схему TeamNumberItem {id, phone_number, team{id,name}} — без чувствительного
    # контекста учёток (login/app_name/note/label/is_active/created_at/updated_at),
    # доступного только на эндпоинтах страницы «СМС» под матрицей sms:*.
    async with sms_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="Продажи")
            await seed_number(
                s,
                phone_number="+13105550800",
                team_id=team.id,
                label="L",
                login="acme",
                app_name="WhatsApp",
                note="секрет",
            )
            team_id = team.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            ok = await c.get(f"/api/teams/{team_id}/numbers")
            missing = await c.get(f"/api/teams/{uuid.uuid4()}/numbers")

    assert ok.status_code == 200
    numbers = ok.json()["numbers"]
    assert [n["phone_number"] for n in numbers] == ["+13105550800"]
    item = numbers[0]
    # Присутствует минимальный набор: id, phone_number, team{id,name}.
    assert set(item.keys()) == {"id", "phone_number", "team"}
    assert item["team"] == {"id": str(team_id), "name": "Продажи"}
    # Чувствительный контекст учёток НЕ утекает через teams:view.
    for leaked in ("login", "app_name", "note", "label", "is_active", "created_at", "updated_at"):
        assert leaked not in item

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "team_not_found"
