"""Integration S3 (ADR-044 §5): глобальный каталог тегов — CRUD, дубль, builtin, rules.

FastAPI-app + реальный Postgres (теги в БД CRM, агрегатор не проксируется). Проверяет:
создание/правку/удаление тега; дубль имени → 409 mail_conflict; удаление builtin → 409;
CRUD правил; apply-to-existing применяет правила к существующим письмам; чтение под
`mail:view`, управление под `mail:tags`. Коды по `error.code`.
"""

from __future__ import annotations

from typing import Any

from mail_s34_helpers import (
    FakeMailClient,
    build_app,
    build_principal,
    client,
    dt,
    mail_db,
    seed_account,
    seed_message,
    seed_tag,
    seed_team,
)


def _admin(sm: Any) -> Any:
    return build_app(sm, build_principal(is_superadmin=True), mail_client=FakeMailClient())


# --- Создание / дубль --------------------------------------------------------
async def test_create_tag_returns_uuid() -> None:
    async with mail_db() as sm, client(_admin(sm)) as c:
        resp = await c.post("/api/mail/tags", json={"name": "Важное", "color": "#ff0000"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Важное"
    assert body["is_builtin"] is False
    assert body["match_mode"] == "any"
    # id — UUID (36 символов с дефисами).
    assert len(body["id"]) == 36 and body["id"].count("-") == 4


async def test_duplicate_name_409() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            await seed_tag(s, name="Существует")
            await s.commit()
        async with client(_admin(sm)) as c:
            resp = await c.post("/api/mail/tags", json={"name": "Существует", "color": "#00ff00"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "mail_conflict"


# --- Правка / удаление / builtin --------------------------------------------
async def test_update_tag_name_and_color() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            tag = await seed_tag(s, name="Старое", color="#111111")
            await s.commit()
            tag_id = str(tag.id)
        async with client(_admin(sm)) as c:
            resp = await c.patch(
                f"/api/mail/tags/{tag_id}", json={"name": "Новое", "color": "#222222"}
            )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Новое"
    assert resp.json()["color"] == "#222222"


async def test_update_to_existing_name_409() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            await seed_tag(s, name="Занято")
            tag = await seed_tag(s, name="Моё")
            await s.commit()
            tag_id = str(tag.id)
        async with client(_admin(sm)) as c:
            resp = await c.patch(f"/api/mail/tags/{tag_id}", json={"name": "Занято"})
    assert resp.status_code == 409


async def test_delete_custom_tag_204() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            tag = await seed_tag(s, name="Удаляемый", is_builtin=False)
            await s.commit()
            tag_id = str(tag.id)
        async with client(_admin(sm)) as c:
            resp = await c.delete(f"/api/mail/tags/{tag_id}")
            listing = await c.get("/api/mail/tags")
    assert resp.status_code == 204
    assert all(t["id"] != tag_id for t in listing.json()["tags"])


async def test_delete_builtin_tag_409() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            tag = await seed_tag(s, name="Встроенный", is_builtin=True)
            await s.commit()
            tag_id = str(tag.id)
        async with client(_admin(sm)) as c:
            resp = await c.delete(f"/api/mail/tags/{tag_id}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "mail_conflict"


async def test_delete_nonexistent_tag_404() -> None:
    async with mail_db() as sm:
        import uuid

        async with client(_admin(sm)) as c:
            resp = await c.delete(f"/api/mail/tags/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_tag_not_found"


# --- Правила -----------------------------------------------------------------
async def test_create_and_delete_rule() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            tag = await seed_tag(s, name="СПравилами")
            await s.commit()
            tag_id = str(tag.id)
        async with client(_admin(sm)) as c:
            created = await c.post(
                f"/api/mail/tags/{tag_id}/rules",
                json={"type": "subject_contains", "pattern": "счёт"},
            )
            assert created.status_code == 201
            rule_id = created.json()["id"]
            listing = await c.get("/api/mail/tags")
            deleted = await c.delete(f"/api/mail/tags/{tag_id}/rules/{rule_id}")
    assert created.json()["type"] == "subject_contains"
    assert any(
        r["id"] == rule_id for t in listing.json()["tags"] if t["id"] == tag_id for r in t["rules"]
    )
    assert deleted.status_code == 204


async def test_rule_on_nonexistent_tag_404() -> None:
    async with mail_db() as sm:
        import uuid

        async with client(_admin(sm)) as c:
            resp = await c.post(
                f"/api/mail/tags/{uuid.uuid4()}/rules",
                json={"type": "subject_contains", "pattern": "x"},
            )
    assert resp.status_code == 404


# --- apply-to-existing -------------------------------------------------------
async def test_apply_to_existing_tags_matching_messages() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt(), subject="Ваш счёт готов")
            await seed_message(s, account_id=1, uid=2, internal_date=dt(), subject="Просто письмо")
            tag = await seed_tag(s, name="Счета")
            await s.commit()
            tag_id = str(tag.id)
        async with client(_admin(sm)) as c:
            # Правило: subject содержит слово «счёт».
            await c.post(
                f"/api/mail/tags/{tag_id}/rules",
                json={"type": "subject_contains", "pattern": "счёт"},
            )
            resp = await c.post(f"/api/mail/tags/{tag_id}/apply-to-existing")
            feed = await c.get("/api/mail/messages", params={"limit": 200})
    assert resp.status_code == 200
    assert resp.json()["applied_count"] == 1  # только письмо со «счёт»
    # Письмо с темой «Ваш счёт готов» получило тег.
    tagged = {m["subject"]: [t["name"] for t in m["tags"]] for m in feed.json()["messages"]}
    assert tagged["Ваш счёт готов"] == ["Счета"]
    assert tagged["Просто письмо"] == []


# --- RBAC: mail:view не может управлять тегами -------------------------------
async def test_view_only_cannot_create_tag() -> None:
    async with mail_db() as sm:
        principal = build_principal(
            is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.post("/api/mail/tags", json={"name": "X", "color": "#000000"})
    assert resp.status_code == 403


async def test_view_can_list_tags() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            await seed_tag(s, name="Видимый")
            await s.commit()
        principal = build_principal(
            is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal, mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get("/api/mail/tags")
    assert resp.status_code == 200
    assert {t["name"] for t in resp.json()["tags"]} == {"Видимый"}
