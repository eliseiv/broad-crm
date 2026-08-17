"""Integration: POST /api/external/knowledge-bot/link + GET user-access (ADR-076).

Реальный Postgres. X-API-Key = DOCUMENTS_API_KEY. Telegram Bot API не вызывается.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.superadmin import SUPERADMIN_USER_ID
from broadcast_helpers import (
    configure_documents_key,
    seed_knowledge_link,
    seed_mail_link,
    seed_role,
    seed_sms_link,
    seed_user,
    sms_db,
)
from documents_helpers import build_app, build_principal, client
from sqlalchemy import text as sa_text

_KEY = "secret-external-key-123"
_HDR = {"X-API-Key": _KEY}


def _app(sm: object) -> object:
    return build_app(sm, build_principal())


async def _link_row(sm: object, telegram_user_id: int) -> dict[str, object] | None:
    async with sm() as s:  # type: ignore[union-attr]
        row = (
            await s.execute(
                sa_text(
                    "SELECT telegram_user_id, user_id, username, started_at, dead_at "
                    "FROM knowledge_bot_links WHERE telegram_user_id = :tid"
                ),
                {"tid": telegram_user_id},
            )
        ).mappings().first()
    return dict(row) if row is not None else None


async def test_link_bootstrap_by_username_creates_row(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_documents_key(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, name="Оператор", permissions={"documents": ["view"]})
            user = await seed_user(s, role, username="Никита", telegram="nikita_01")
            await s.commit()
            user_id = user.id
        app = _app(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/external/knowledge-bot/link",
                headers=_HDR,
                json={"telegram_user_id": 7001, "username": "@Nikita_01"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == str(user_id)
    assert body["role_name"] == "Оператор"
    assert resp.headers.get("Cache-Control") == "no-store"
    row = await _link_row(sm, 7001)
    assert row is not None
    assert str(row["user_id"]) == str(user_id)
    assert row["username"] == "nikita_01"
    assert row["dead_at"] is None


async def test_link_repeat_keeps_started_at_and_clears_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_documents_key(monkeypatch)
    started = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role, telegram="repeat_user")
            await seed_knowledge_link(
                s,
                telegram_user_id=7002,
                user_id=user.id,
                username="repeat_user",
                started_at=started,
                dead_at=datetime(2026, 2, 1, tzinfo=UTC),
            )
            await s.commit()
        app = _app(sm)
        async with client(app) as c:
            first = await c.post(
                "/api/external/knowledge-bot/link",
                headers=_HDR,
                json={"telegram_user_id": 7002, "username": "repeat_user"},
            )
            second = await c.post(
                "/api/external/knowledge-bot/link",
                headers=_HDR,
                json={"telegram_user_id": 7002, "username": "repeat_user"},
            )
    assert first.status_code == 200
    assert second.status_code == 200
    row = await _link_row(sm, 7002)
    assert row is not None
    assert row["dead_at"] is None
    assert row["started_at"] == started


async def test_link_unknown_telegram_is_404_and_no_row(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_documents_key(monkeypatch)
    async with sms_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/external/knowledge-bot/link",
                headers=_HDR,
                json={"telegram_user_id": 7999, "username": "nobody"},
            )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_linked"
    assert await _link_row(sm, 7999) is None


async def test_link_inactive_user_is_404_and_no_row(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_documents_key(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await seed_user(s, role, telegram="off_user", is_active=False)
            await s.commit()
        app = _app(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/external/knowledge-bot/link",
                headers=_HDR,
                json={"telegram_user_id": 7003, "username": "off_user"},
            )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_linked"
    assert await _link_row(sm, 7003) is None


async def test_link_system_anchor_is_404_and_no_row(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_documents_key(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            await seed_knowledge_link(
                s, telegram_user_id=7004, user_id=SUPERADMIN_USER_ID, username="sys"
            )
            await s.commit()
        app = _app(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/external/knowledge-bot/link",
                headers=_HDR,
                json={"telegram_user_id": 7004, "username": "sys"},
            )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "user_not_linked"


async def test_link_empty_key_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENTS_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    async with sms_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/external/knowledge-bot/link",
                headers=_HDR,
                json={"telegram_user_id": 1, "username": "x"},
            )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "documents_external_not_configured"


async def test_link_wrong_key_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_documents_key(monkeypatch)
    async with sms_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/external/knowledge-bot/link",
                headers={"X-API-Key": "nope"},
                json={"telegram_user_id": 1, "username": "x"},
            )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


async def test_user_access_knowledge_link_wins_over_sms_mail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_documents_key(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role_kb = await seed_role(s, name="KB", permissions={"documents": ["view"]})
            role_sms = await seed_role(s, name="SMS", permissions={"sms": ["view"]})
            kb_user = await seed_user(s, role_kb, username="kb_user")
            sms_user = await seed_user(s, role_sms, username="sms_user")
            await seed_knowledge_link(s, telegram_user_id=8100, user_id=kb_user.id)
            await seed_sms_link(s, telegram_user_id=8100, user_id=sms_user.id)
            await seed_mail_link(s, telegram_user_id=8100, user_id=sms_user.id, username="x")
            await s.commit()
            kb_id = kb_user.id
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get("/api/external/documents/user-access/8100", headers=_HDR)
    assert resp.status_code == 200
    assert resp.json()["user_id"] == str(kb_id)
    assert resp.json()["role_name"] == "KB"


async def test_user_access_username_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_documents_key(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, name="Boot", permissions={"documents": ["view"]})
            user = await seed_user(s, role, telegram="boot_nick")
            await s.commit()
            user_id = user.id
        app = _app(sm)
        async with client(app) as c:
            missing = await c.get("/api/external/documents/user-access/8200", headers=_HDR)
            found = await c.get(
                "/api/external/documents/user-access/8200?username=@Boot_Nick",
                headers=_HDR,
            )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "user_not_linked"
    assert found.status_code == 200
    assert found.json()["user_id"] == str(user_id)
    # GET user-access не пишет линк.
    assert await _link_row(sm, 8200) is None
