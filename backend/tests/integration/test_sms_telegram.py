"""Integration-тесты Telegram-слоя SMS (link/auth/webhook, 04-api.md#sms, ADR-030).

- `POST /api/sms/telegram/link` — под JWT: upsert линка своего Telegram; супер-админ
  без `uid` → 403; битый/протухший initData → 401.
- `POST /api/sms/telegram/auth` — публичный статус привязки (HMAC init_data), без сессии.
- `POST /api/sms/telegram/webhook` — секрет-токен constant-time (403 при несовпадении);
  `/start` → sendMessage с web_app-кнопкой; прочее → 200 no-op.
initData/секреты в тестах не логируются.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from app.models.sms_telegram_link import SmsTelegramLink
from sms_helpers import (
    build_app,
    build_principal,
    client,
    seed_link,
    seed_role,
    seed_user,
    sms_db,
)
from sqlalchemy import select

_BOT_TOKEN = "555000:SMS-BOT-TOKEN"
_WEBHOOK_SECRET = "webhook-secret-xyz"
_WEBAPP_URL = "https://crm.example.com/sms-webapp"


def _make_init_data(*, bot_token: str = _BOT_TOKEN, telegram_user_id: int, auth_date: int) -> str:
    fields: dict[str, str] = {
        "user": json.dumps({"id": telegram_user_id, "first_name": "Оля"}),
        "auth_date": str(auth_date),
        "query_id": "AAA",
    }
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _set_sms_env(monkeypatch: Any) -> None:
    from app.config import get_settings

    monkeypatch.setenv("SMS_TELEGRAM_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setenv("SMS_TELEGRAM_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    monkeypatch.setenv("SMS_TELEGRAM_WEBAPP_URL", _WEBAPP_URL)
    get_settings.cache_clear()


# --- link -------------------------------------------------------------------


async def test_link_upserts_own_telegram(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role)
            user_id = user.id
            await s.commit()
        principal = build_principal(user_id=user_id, is_superadmin=False, permissions={"sms": []})
        app = build_app(sm, principal)
        init_data = _make_init_data(telegram_user_id=777001, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/link", json={"init_data": init_data})
        async with sm() as s:
            link = (
                await s.execute(
                    select(SmsTelegramLink).where(SmsTelegramLink.telegram_user_id == 777001)
                )
            ).scalar_one()

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"linked": True, "telegram_user_id": 777001}
    assert link.user_id == user_id
    assert link.dead_at is None


async def test_link_superadmin_without_uid_is_403(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())  # супер-админ, user_id=None
        init_data = _make_init_data(telegram_user_id=777002, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/link", json={"init_data": init_data})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_link_invalid_init_data_is_401(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role)
            user_id = user.id
            await s.commit()
        principal = build_principal(user_id=user_id, is_superadmin=False, permissions={"sms": []})
        app = build_app(sm, principal)
        # Подпись под чужим токеном → hash_mismatch → 401 invalid_init_data.
        bad = _make_init_data(bot_token="999:WRONG", telegram_user_id=1, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/link", json={"init_data": bad})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_init_data"


# --- auth (публичный) -------------------------------------------------------


async def test_auth_reports_linked_true_when_active_link(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role)
            await seed_link(s, telegram_user_id=888001, user_id=user.id)
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(telegram_user_id=888001, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
    assert resp.status_code == 200
    assert resp.json() == {"linked": True, "telegram_user_id": 888001}


async def test_auth_reports_linked_false_when_no_link(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        init_data = _make_init_data(telegram_user_id=888002, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
    assert resp.status_code == 200
    assert resp.json() == {"linked": False, "telegram_user_id": 888002}


async def test_auth_expired_init_data_is_401(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        stale = _make_init_data(telegram_user_id=1, auth_date=int(time.time()) - 48 * 3600)
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": stale})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "init_data_expired"


# --- webhook (секрет-токен) -------------------------------------------------


async def test_webhook_wrong_secret_is_403(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/sms/telegram/webhook",
                json={"message": {"text": "/start"}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "WRONG"},
            )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "invalid_webhook_secret"


async def test_webhook_non_start_is_noop_200(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/sms/telegram/webhook",
                json={"message": {"text": "привет", "chat": {"id": 5}}},
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )
    assert resp.status_code == 200


async def test_webhook_start_sends_webapp_button(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    import app.api.sms_webhooks as webhooks_module

    sent: list[dict[str, Any]] = []

    class _RecordingBot:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            self.is_configured = True

        async def send_message(
            self, chat_id: int, text: str, *, reply_markup: Any = None
        ) -> dict[str, Any]:
            sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
            return {"ok": True}

    monkeypatch.setattr(webhooks_module, "SmsBotClient", _RecordingBot)

    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/sms/telegram/webhook",
                json={"message": {"text": "/start", "chat": {"id": 4242}}},
                headers={"X-Telegram-Bot-Api-Secret-Token": _WEBHOOK_SECRET},
            )

    assert resp.status_code == 200
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 4242
    button = sent[0]["reply_markup"]["inline_keyboard"][0][0]
    assert button["web_app"]["url"] == _WEBAPP_URL
