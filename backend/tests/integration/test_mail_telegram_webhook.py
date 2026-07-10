"""Integration S4 (ADR-044 §6/§9): вебхуки почтовых ботов + callback authz.

Реальный Postgres + FastAPI-app; Bot API замокан (сервис не шлёт наружу). Проверяет:
основной webhook — mismatch секрета → 404 (не 403); push-webhook — header-only fail-closed
→ 404, неизвестный `bot_name` → 404; callback основного бота — visibility (участник
команды/admin), чужой → отказ; push-callback — авторизация `MAIL_ADMIN_TELEGRAM_IDS` +
team-match (подделка `mail:{id}` чужой команды блокируется).
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pytest
from mail_s34_helpers import (
    build_app,
    build_principal,
    client,
    dt,
    mail_db,
    seed_account,
    seed_link,
    seed_message,
    seed_role,
    seed_team,
    seed_user,
)
from sqlalchemy import text as sa_text

_WEBHOOK_SECRET = "main-webhook-secret-xyz"
_BOT_TOKEN = "111:main-bot"
_PUSH_SECRET = "push-secret-ivan"
_PUSH_TOKEN = "222:ivan-bot"
_TEAM_UUID = "33333333-3333-3333-3333-333333333333"


class _RecordingBot:
    """Фейк MailBotClient, инстанцируемый внутри сервиса: пишет отправки/ответы глобально."""

    sent: ClassVar[list[tuple[int, str]]] = []
    answered: ClassVar[list[tuple[str, str | None]]] = []

    def __init__(self, token: str = "", proxy_url: str = "") -> None:
        self.token = token

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    async def send_message(
        self, chat_id: int, text: str, *, parse_mode: str | None = None, reply_markup: Any = None
    ) -> dict[str, Any]:
        type(self).sent.append((chat_id, text))
        return {"ok": True}

    async def answer_callback_query(
        self, callback_query_id: str, *, text: str | None = None, show_alert: bool = False
    ) -> None:
        type(self).answered.append((callback_query_id, text))


@pytest.fixture(autouse=True)
def _reset_bot() -> Any:
    _RecordingBot.sent = []
    _RecordingBot.answered = []
    yield


def _patch_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменяет MailBotClient в сервисе Telegram на записывающий фейк."""
    import app.services.mail_telegram_service as mod

    monkeypatch.setattr(mod, "MailBotClient", _RecordingBot)


async def _enable_main_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_BOT_TOKEN", _BOT_TOKEN)
    monkeypatch.setenv("MAIL_BOT_WEBHOOK_SECRET", _WEBHOOK_SECRET)
    get_settings.cache_clear()


async def _enable_push_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_BOT_IVAN_TOKEN", _PUSH_TOKEN)
    monkeypatch.setenv("MAIL_BOT_IVAN_WEBHOOK_SECRET", _PUSH_SECRET)
    monkeypatch.setenv("MAIL_BOT_IVAN_TEAM_ID", _TEAM_UUID)
    monkeypatch.setenv("MAIL_ADMIN_TELEGRAM_IDS", "700,701")
    get_settings.cache_clear()


# --- Основной webhook: mismatch секрета → 404 -------------------------------
async def test_main_webhook_wrong_secret_404(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_main_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/webhook/WRONG", json={"message": {}})
    assert resp.status_code == 404


async def test_main_webhook_header_mismatch_404(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_main_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                f"/api/mail/telegram/webhook/{_WEBHOOK_SECRET}",
                json={"message": {}},
                headers={"X-Telegram-Bot-Api-Secret-Token": "bad"},
            )
    assert resp.status_code == 404


async def test_main_webhook_correct_secret_200(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_main_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            # /start от несопоставленного ника → orphan-линк + приветствие (200).
            resp = await c.post(
                f"/api/mail/telegram/webhook/{_WEBHOOK_SECRET}",
                json={
                    "message": {
                        "text": "/start",
                        "chat": {"id": 900},
                        "from": {"id": 900, "username": "ghost"},
                    }
                },
            )
            async with sm() as s:
                link = (
                    await s.execute(
                        sa_text(
                            "SELECT username, user_id FROM mail_telegram_links "
                            "WHERE telegram_user_id=900"
                        )
                    )
                ).first()
    assert resp.status_code == 200
    assert link is not None
    assert link[0] == "ghost"  # username нормализован в нижний регистр
    assert link[1] is None  # orphan (нет CRM-пользователя)


# --- Push-webhook: header-only fail-closed → 404 -----------------------------
async def test_push_webhook_missing_header_404(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_push_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post("/api/mail/telegram/push-webhook/ivan", json={"callback_query": {}})
    assert resp.status_code == 404  # секрет header-only; отсутствует → 404


async def test_push_webhook_unknown_bot_404(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_push_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                "/api/mail/telegram/push-webhook/unknownbot",
                json={"callback_query": {}},
                headers={"X-Telegram-Bot-Api-Secret-Token": _PUSH_SECRET},
            )
    assert resp.status_code == 404  # неизвестный bot_name неотличим от неверного секрета


async def test_push_webhook_correct_secret_200(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_push_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                "/api/mail/telegram/push-webhook/ivan",
                json={"message": {}},  # не callback → 200 no-op
                headers={"X-Telegram-Bot-Api-Secret-Token": _PUSH_SECRET},
            )
    assert resp.status_code == 200


# --- Callback основного бота: visibility ------------------------------------
def _callback_update(*, tg_user_id: int, chat_id: int, message_id: int) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "cb1",
            "from": {"id": tg_user_id},
            "message": {"chat": {"id": chat_id}},
            "data": f"mail:{message_id}",
        }
    }


async def test_callback_member_sees_message(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_main_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            u1 = await seed_user(s, role, telegram="u1")
            team = await seed_team(s)
            await s.execute(
                sa_text("INSERT INTO user_teams (user_id, team_id) VALUES (:u,:t)"),
                {"u": u1.id, "t": team.id},
            )
            await seed_link(s, telegram_user_id=101, user_id=u1.id, username="u1")
            await seed_account(s, account_id=1, team_id=team.id)
            msg = await seed_message(s, account_id=1, uid=1, internal_date=dt(), subject="Тело")
            await s.commit()
            mid = msg.id
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                f"/api/mail/telegram/webhook/{_WEBHOOK_SECRET}",
                json=_callback_update(tg_user_id=101, chat_id=101, message_id=mid),
            )
    assert resp.status_code == 200
    # Участник команды ящика → тело письма отправлено.
    assert any("Тело" in text for _, text in _RecordingBot.sent)


async def test_callback_non_member_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_main_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            # Пользователь НЕ в команде ящика (и не admin).
            outsider = await seed_user(s, role, telegram="out")
            owner_team = await seed_team(s)
            await seed_link(s, telegram_user_id=202, user_id=outsider.id, username="out")
            await seed_account(s, account_id=1, team_id=owner_team.id)
            msg = await seed_message(s, account_id=1, uid=1, internal_date=dt(), subject="Секрет")
            await s.commit()
            mid = msg.id
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                f"/api/mail/telegram/webhook/{_WEBHOOK_SECRET}",
                json=_callback_update(tg_user_id=202, chat_id=202, message_id=mid),
            )
    assert resp.status_code == 200
    # Не участник и не admin → тело НЕ отправлено, только отказ-answer.
    assert all("Секрет" not in text for _, text in _RecordingBot.sent)
    assert _RecordingBot.answered  # был answerCallbackQuery с отказом


# --- Push-callback: авторизация admin + team-match ---------------------------
def _push_callback(*, tg_user_id: int, message_id: int) -> dict[str, Any]:
    return {
        "callback_query": {
            "id": "cb2",
            "from": {"id": tg_user_id},
            "message": {"chat": {"id": tg_user_id}},
            "data": f"mail:{message_id}",
        }
    }


async def test_push_callback_foreign_team_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_push_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            # Письмо принадлежит ДРУГОЙ команде (не команде бота ivan = _TEAM_UUID).
            other_team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=other_team.id)
            msg = await seed_message(s, account_id=1, uid=1, internal_date=dt(), subject="Чужое")
            await s.commit()
            mid = msg.id
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                "/api/mail/telegram/push-webhook/ivan",
                json=_push_callback(tg_user_id=700, message_id=mid),  # 700 ∈ admin ids
                headers={"X-Telegram-Bot-Api-Secret-Token": _PUSH_SECRET},
            )
    assert resp.status_code == 200
    # Подделка mail:{id} чужой команды: тело НЕ отправлено (team_id != bot.team_id).
    assert all("Чужое" not in text for _, text in _RecordingBot.sent)


async def test_push_callback_non_admin_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_push_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="ivan-team")
            # Ящик принадлежит команде бота ivan.
            await s.execute(
                sa_text("UPDATE teams SET id=:tid WHERE id=:orig"),
                {"tid": _TEAM_UUID, "orig": team.id},
            )
            await seed_account(s, account_id=1, team_id=uuid.UUID(_TEAM_UUID))
            msg = await seed_message(s, account_id=1, uid=1, internal_date=dt(), subject="Данные")
            await s.commit()
            mid = msg.id
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                "/api/mail/telegram/push-webhook/ivan",
                json=_push_callback(tg_user_id=999, message_id=mid),  # 999 НЕ в admin ids
                headers={"X-Telegram-Bot-Api-Secret-Token": _PUSH_SECRET},
            )
    assert resp.status_code == 200
    assert all("Данные" not in text for _, text in _RecordingBot.sent)


async def test_push_callback_admin_own_team_delivers(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_push_bot(monkeypatch)
    _patch_bot(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s, name="ivan-team")
            await s.execute(
                sa_text("UPDATE teams SET id=:tid WHERE id=:orig"),
                {"tid": _TEAM_UUID, "orig": team.id},
            )
            await seed_account(s, account_id=1, team_id=uuid.UUID(_TEAM_UUID))
            msg = await seed_message(
                s, account_id=1, uid=1, internal_date=dt(), subject="Разрешено"
            )
            await s.commit()
            mid = msg.id
        app = build_app(sm, build_principal(is_superadmin=True))
        async with client(app) as c:
            resp = await c.post(
                "/api/mail/telegram/push-webhook/ivan",
                json=_push_callback(tg_user_id=700, message_id=mid),  # admin + своя команда
                headers={"X-Telegram-Bot-Api-Secret-Token": _PUSH_SECRET},
            )
    assert resp.status_code == 200
    assert any("Разрешено" in text for _, text in _RecordingBot.sent)
