"""GET /api/users: bot_started только от активного knowledge-линка (ADR-076)."""

from __future__ import annotations

from datetime import UTC, datetime

from broadcast_helpers import (
    seed_knowledge_link,
    seed_mail_link,
    seed_role,
    seed_user,
    sms_db,
)
from sms_helpers import build_app, build_principal, client, seed_link


async def test_users_bot_started_true_only_for_active_knowledge_link() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, name="Оператор")
            with_kb = await seed_user(s, role, username="СБотом")
            with_sms = await seed_user(s, role, username="СмсЛинк")
            with_mail = await seed_user(s, role, username="ПочтаЛинк")
            dead_kb = await seed_user(s, role, username="МёртвыйЛинк")
            await seed_user(s, role, username="БезЛинка")
            await seed_knowledge_link(s, telegram_user_id=9601, user_id=with_kb.id)
            await seed_link(s, telegram_user_id=9602, user_id=with_sms.id)
            await seed_mail_link(s, telegram_user_id=9603, user_id=with_mail.id, username="m")
            await seed_knowledge_link(
                s,
                telegram_user_id=9604,
                user_id=dead_kb.id,
                dead_at=datetime.now(UTC),
            )
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get("/api/users")
    assert resp.status_code == 200
    by_name = {u["username"]: u for u in resp.json()["items"]}
    assert by_name["СБотом"]["bot_started"] is True
    assert by_name["СмсЛинк"]["bot_started"] is False
    assert by_name["ПочтаЛинк"]["bot_started"] is False
    assert by_name["МёртвыйЛинк"]["bot_started"] is False
    assert by_name["БезЛинка"]["bot_started"] is False
    assert "telegram_user_id" not in resp.text
    assert "started_at" not in resp.text
