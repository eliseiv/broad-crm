"""Integration-тесты Telegram-слоя SMS (link/auth/webhook, 04-api.md#sms, ADR-030).

- `POST /api/sms/telegram/link` — под JWT: upsert линка своего Telegram; супер-админ
  без `uid` → 403; битый/протухший initData → 401.
- `POST /api/sms/telegram/auth` — публичный беспарольный Telegram-SSO (ADR-031): резолв
  оператора по Telegram-идентичности → upsert/revive линка → выдача CRM access-JWT.
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
from app.models.user import User
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


def _make_init_data(
    *,
    bot_token: str = _BOT_TOKEN,
    telegram_user_id: int,
    auth_date: int,
    username: str | None = None,
) -> str:
    user_obj: dict[str, Any] = {"id": telegram_user_id, "first_name": "Оля"}
    if username is not None:
        user_obj["username"] = username
    fields: dict[str, str] = {
        "user": json.dumps(user_obj),
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


# --- auth: беспарольный Telegram-SSO (ADR-031) ------------------------------


def _decode(token: str) -> Any:
    """Декодирует access-JWT (тем же секретом, что выпуск) для проверки claim'ов."""
    from app.infra.jwt import decode_access_token

    return decode_access_token(token)


async def test_auth_sso_existing_active_link_returns_jwt(monkeypatch: Any) -> None:
    # id-first: живой линк на активного оператора → 200 + CRM access-JWT.
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, name="Оператор", permissions={"sms": ["view"]})
            user = await seed_user(s, role, username="crm_bob")
            await seed_link(s, telegram_user_id=888001, user_id=user.id)
            user_id = user.id
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(telegram_user_id=888001, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["telegram_user_id"] == 888001
    assert body["linked"] is True
    assert isinstance(body["expires_in"], int) and body["expires_in"] > 0
    claims = _decode(body["access_token"])
    assert claims.sub == "crm_bob"  # sub = users.username, НЕ Telegram
    assert claims.uid == str(user_id)
    assert claims.superadmin is False  # оператор не входит суперадмином


async def test_auth_sso_revives_dead_link(monkeypatch: Any) -> None:
    from datetime import UTC, datetime

    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role, username="crm_dead")
            await seed_link(s, telegram_user_id=888010, user_id=user.id, dead_at=datetime.now(UTC))
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(telegram_user_id=888010, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
        async with sm() as s:
            link = (
                await s.execute(
                    select(SmsTelegramLink).where(SmsTelegramLink.telegram_user_id == 888010)
                )
            ).scalar_one()

    assert resp.status_code == 200
    assert resp.json()["linked"] is True
    assert link.dead_at is None  # мёртвый линк оживлён


async def test_auth_sso_username_bootstrap_upserts_link(monkeypatch: Any) -> None:
    # Линка нет, но users.telegram == normalize(username из initData) → upsert линка + JWT.
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role, username="crm_alice", telegram="operator_alice")
            user_id = user.id
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(
            telegram_user_id=888020, auth_date=int(time.time()), username="Operator_Alice"
        )
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
        async with sm() as s:
            link = (
                await s.execute(
                    select(SmsTelegramLink).where(SmsTelegramLink.telegram_user_id == 888020)
                )
            ).scalar_one()

    assert resp.status_code == 200
    assert _decode(resp.json()["access_token"]).sub == "crm_alice"
    assert link.user_id == user_id  # линк создан bootstrap'ом


async def test_auth_sso_id_first_takes_precedence_over_username(monkeypatch: Any) -> None:
    # Линк указывает на userA; username в initData совпадает с userB.telegram —
    # первичен telegram_user_id (устаревший users.telegram не мешает).
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user_a = await seed_user(s, role, username="crm_a")
            await seed_user(s, role, username="crm_b", telegram="tg_b")
            await seed_link(s, telegram_user_id=888030, user_id=user_a.id)
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(
            telegram_user_id=888030, auth_date=int(time.time()), username="tg_b"
        )
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})

    assert resp.status_code == 200
    assert _decode(resp.json()["access_token"]).sub == "crm_a"  # id-first, не crm_b


async def test_auth_sso_first_login_at_idempotent(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role, username="crm_first")
            await seed_link(s, telegram_user_id=888040, user_id=user.id)
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(telegram_user_id=888040, auth_date=int(time.time()))
        async with client(app) as c:
            await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
        async with sm() as s:
            first = (
                (await s.execute(select(User).where(User.username == "crm_first")))
                .scalar_one()
                .first_login_at
            )
        async with client(app) as c:
            await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
        async with sm() as s:
            second = (
                (await s.execute(select(User).where(User.username == "crm_first")))
                .scalar_one()
                .first_login_at
            )

    assert first is not None
    assert second == first  # второй вход НЕ перезаписывает first_login_at


async def test_auth_sso_no_link_no_username_is_403(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        init_data = _make_init_data(telegram_user_id=888050, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "sms_operator_not_provisioned"


async def test_auth_sso_username_no_match_is_403(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        init_data = _make_init_data(
            telegram_user_id=888051, auth_date=int(time.time()), username="nobody"
        )
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "sms_operator_not_provisioned"


async def test_auth_sso_username_matches_inactive_user_is_403(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            await seed_user(s, role, username="crm_off", telegram="operator_off", is_active=False)
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(
            telegram_user_id=888052, auth_date=int(time.time()), username="operator_off"
        )
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "sms_operator_not_provisioned"


async def test_auth_sso_link_to_inactive_user_is_403(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role, username="crm_disabled", is_active=False)
            await seed_link(s, telegram_user_id=888053, user_id=user.id)
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(telegram_user_id=888053, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "sms_operator_not_provisioned"


async def test_auth_empty_init_data_is_400(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": ""})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


async def test_auth_invalid_hmac_is_401(monkeypatch: Any) -> None:
    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        bad = _make_init_data(bot_token="999:WRONG", telegram_user_id=1, auth_date=int(time.time()))
        async with client(app) as c:
            resp = await c.post("/api/sms/telegram/auth", json={"init_data": bad})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_init_data"


async def test_auth_init_data_and_username_not_logged(monkeypatch: Any) -> None:
    import structlog

    _set_sms_env(monkeypatch)
    async with sms_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role, username="crm_secret")
            await seed_link(s, telegram_user_id=888060, user_id=user.id)
            await s.commit()
        app = build_app(sm, build_principal())
        init_data = _make_init_data(
            telegram_user_id=888060, auth_date=int(time.time()), username="SECRET_TG_NAME"
        )
        with structlog.testing.capture_logs() as logs:
            async with client(app) as c:
                resp = await c.post("/api/sms/telegram/auth", json={"init_data": init_data})

    assert resp.status_code == 200
    serialized = json.dumps(logs, default=str, ensure_ascii=False)
    assert init_data not in serialized
    assert "SECRET_TG_NAME" not in serialized


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
