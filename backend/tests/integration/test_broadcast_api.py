"""Integration: GET/POST /api/broadcasts (ADR-076). Реальный Postgres, Bot API — FakeBot."""

from __future__ import annotations

import uuid

import pytest
from app.api import deps
from app.domain.permissions import full_catalog_permissions
from app.domain.superadmin import SUPERADMIN_USER_ID
from app.models.user_role import user_roles
from broadcast_helpers import (
    FakeKnowledgeBot,
    enable_knowledge_bot,
    seed_knowledge_link,
    seed_role,
    seed_user,
    sms_db,
)
from sms_helpers import build_app, build_principal, client
from sqlalchemy import insert
from sqlalchemy import text as sa_text


def _install_fake_bot(monkeypatch: pytest.MonkeyPatch, bot: FakeKnowledgeBot) -> None:
    monkeypatch.setattr(deps, "KnowledgeBotClient", lambda _token: bot)


async def test_audience_under_broadcast_view_without_roles_view() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, name="Оператор", permissions={"broadcast": ["view"]})
            started = await seed_user(s, role, username="Стартер")
            await seed_user(s, role, username="БезБота")
            inactive = await seed_user(s, role, username="Выкл", is_active=False)
            await seed_knowledge_link(s, telegram_user_id=9101, user_id=started.id)
            await seed_knowledge_link(s, telegram_user_id=9102, user_id=inactive.id)
            await seed_knowledge_link(
                s, telegram_user_id=9103, user_id=SUPERADMIN_USER_ID, username="sys"
            )
            await s.commit()
        principal = build_principal(
            is_superadmin=False,
            role="Оператор",
            permissions={"broadcast": ["view"]},
        )
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/broadcasts/audience")
    assert resp.status_code == 200
    body = resp.json()
    assert body["roles"]
    by_name = {r["name"]: r for r in body["roles"]}
    assert "Оператор" in by_name
    assert by_name["Оператор"]["started_count"] == 1
    assert by_name["Оператор"]["not_started_count"] == 1
    # Системный якорь и неактивные не в all_*.
    assert body["all_started_count"] == 1
    assert body["all_not_started_count"] == 1


async def test_post_broadcast_empty_token_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_BOT_TOKEN", "")
    from app.config import get_settings

    get_settings.cache_clear()
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "привет", "all": True, "role_ids": []},
            )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "knowledge_bot_not_configured"


async def test_post_broadcast_all_plus_role_ids_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_knowledge_bot(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await s.commit()
            role_id = str(role.id)
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "hi", "all": True, "role_ids": [role_id]},
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"


@pytest.mark.parametrize(
    "text",
    ["", "   ", "x" * 4097],
)
async def test_post_broadcast_invalid_text_is_422(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    enable_knowledge_bot(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": text, "all": True, "role_ids": []},
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"


async def test_post_broadcast_without_send_is_403(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_knowledge_bot(monkeypatch)
    async with sms_db() as sm:
        principal = build_principal(
            is_superadmin=False,
            role="Оператор",
            permissions={"broadcast": ["view"]},
        )
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "hi", "all": True, "role_ids": []},
            )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_users_api_cyrillic_admin_full_catalog_not_403() -> None:
    async with sms_db() as sm:
        principal = build_principal(
            is_superadmin=False,
            role="Админ",
            permissions=full_catalog_permissions(),
        )
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/users")
    assert resp.status_code == 200


async def test_fanout_dedup_same_chat_id_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Два user_id не делят chat_id (PK). Дедуп: один chat_id в адресатах ровно раз.

    Два линка одного user_id с разными chat_id — два сообщения (не дедуп по user).
    Дедуп по telegram_user_id проверяется тем, что повторный PK невозможен; здесь
    два chat_id одного user дают два send — контракт UNIQUE(chat_id).
    """
    enable_knowledge_bot(monkeypatch)
    bot = FakeKnowledgeBot()
    _install_fake_bot(monkeypatch, bot)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, name="Оператор")
            user = await seed_user(s, role, username="ДваЧата")
            await seed_knowledge_link(s, telegram_user_id=9201, user_id=user.id)
            await seed_knowledge_link(s, telegram_user_id=9202, user_id=user.id)
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "один", "all": True, "role_ids": []},
            )
    assert resp.status_code == 200
    assert resp.json()["sent"] == 2
    assert [chat for chat, _ in bot.sent] == [9201, 9202] or {c for c, _ in bot.sent} == {
        9201,
        9202,
    }


async def test_fanout_same_chat_dedup_and_skipped_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два линка одного user_id с разными chat_id — два сообщения; без линка → skipped."""
    enable_knowledge_bot(monkeypatch)
    bot = FakeKnowledgeBot()
    _install_fake_bot(monkeypatch, bot)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, name="Оператор")
            linked = await seed_user(s, role, username="СЛинком")
            await seed_user(s, role, username="БезЛинка")
            await seed_knowledge_link(s, telegram_user_id=9301, user_id=linked.id)
            await seed_knowledge_link(s, telegram_user_id=9302, user_id=linked.id)
            await s.commit()
            role_id = str(role.id)
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "роль", "all": False, "role_ids": [role_id]},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] == 2
    assert body["skipped_not_started"] == 1
    assert body["failed"] == 0
    assert {chat for chat, txt in bot.sent} == {9301, 9302}
    assert all(txt == "роль" for _, txt in bot.sent)


async def test_fanout_dedup_user_with_two_selected_roles_sent_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ DISTINCT-дедуп адресатов (ADR-079 §5): две ВЫБРАННЫЕ роли — одно сообщение.

    M2M-join `users × user_roles` даёт по строке на каждую роль пользователя, поэтому без
    `DISTINCT` носитель обеих выбранных ролей получил бы рассылку дважды, а
    `skipped_not_started` задвоился бы у такого же пользователя без линка.
    """
    enable_knowledge_bot(monkeypatch)
    bot = FakeKnowledgeBot()
    _install_fake_bot(monkeypatch, bot)
    async with sms_db() as sm:
        async with sm() as s:
            role_one = await seed_role(s, name="Первая")
            role_two = await seed_role(s, name="Вторая")
            linked = await seed_user(s, role_one, username="ДвеРоли")
            not_started = await seed_user(s, role_one, username="ДвеРолиБезЛинка")
            # Вторая роль — прямой строкой `user_roles` (seed_user выдаёт ровно одну).
            await s.execute(insert(user_roles).values(user_id=linked.id, role_id=role_two.id))
            await s.execute(insert(user_roles).values(user_id=not_started.id, role_id=role_two.id))
            await seed_knowledge_link(s, telegram_user_id=9601, user_id=linked.id)
            await s.commit()
            role_ids = [str(role_one.id), str(role_two.id)]
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "дедуп", "all": False, "role_ids": role_ids},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] == 1
    assert bot.sent == [(9601, "дедуп")]  # ровно одно сообщение, не два
    assert body["skipped_not_started"] == 1  # не задвоен
    assert body["failed"] == 0


async def test_fanout_telegram_403_marks_dead_and_returns_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_knowledge_bot(monkeypatch)
    bot = FakeKnowledgeBot()
    bot.forbidden_for(9401)
    _install_fake_bot(monkeypatch, bot)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            await seed_knowledge_link(s, telegram_user_id=9401, user_id=user.id)
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "bye", "all": True, "role_ids": []},
            )
        async with sm() as s:
            dead_at = (
                await s.execute(
                    sa_text("SELECT dead_at FROM knowledge_bot_links WHERE telegram_user_id=9401")
                )
            ).scalar_one()
    assert resp.status_code == 200
    assert resp.json()["failed"] == 1
    assert resp.json()["sent"] == 0
    assert dead_at is not None


async def test_fanout_partial_success_is_200(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_knowledge_bot(monkeypatch)
    bot = FakeKnowledgeBot()
    bot.forbidden_for(9501)
    bot.error_for(9502)
    _install_fake_bot(monkeypatch, bot)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            u1 = await seed_user(s, role, username="A")
            u2 = await seed_user(s, role, username="B")
            u3 = await seed_user(s, role, username="C")
            await seed_knowledge_link(s, telegram_user_id=9501, user_id=u1.id)
            await seed_knowledge_link(s, telegram_user_id=9502, user_id=u2.id)
            await seed_knowledge_link(s, telegram_user_id=9503, user_id=u3.id)
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={"text": "частично", "all": True, "role_ids": []},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] == 1
    assert body["failed"] == 2
    assert body["skipped_not_started"] == 0
    assert bot.sent == [(9503, "частично")]


async def test_post_broadcast_unknown_role_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_knowledge_bot(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/broadcasts",
                json={
                    "text": "hi",
                    "all": False,
                    "role_ids": [str(uuid.uuid4())],
                },
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"
