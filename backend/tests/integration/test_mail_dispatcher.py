"""Integration S4 (ADR-044 §6): фоновый Telegram-диспетчер — проходы A/B/C + дедуп.

Реальный Postgres; Bot API замокан `FakeMailBot` (реальных вызовов в Telegram нет).
`poll_once()` вызывается синхронно. Проверяет: проход A (резолв получателей команды +
admin-уровень, минус opt-out, минус мёртвая привязка; `notified_at` ставится ПОСЛЕ
резервирования); дедуп `try_reserve`; проход B (транзиентный сбой → failed → recovery
досылает; attempts растёт; attempts>=max → dead, не вечный цикл); проход C (guarded
`down_alert_sent_at`; 7 down со штампом не порождают алертов; сброс на re-enable).
"""

from __future__ import annotations

from typing import Any

import pytest
from app.infra.mail_telegram import MailTelegramApiError, MailTelegramForbiddenError
from mail_s34_helpers import (
    dt,
    mail_db,
    seed_account,
    seed_link,
    seed_message,
    seed_notification,
    seed_role,
    seed_team,
    seed_user,
    seed_user_settings,
)
from sqlalchemy import text as sa_text


class FakeMailBot:
    """Фейк MailBotClient: пишет отправки; программируемые сбои по chat_id."""

    def __init__(self, *, is_configured: bool = True) -> None:
        self.is_configured = is_configured
        self.sent: list[tuple[int, str]] = []
        self._forbidden: set[int] = set()
        self._api_error: set[int] = set()

    def forbidden_for(self, chat_id: int) -> None:
        self._forbidden.add(chat_id)

    def api_error_for(self, chat_id: int) -> None:
        self._api_error.add(chat_id)

    def clear_errors(self) -> None:
        self._forbidden.clear()
        self._api_error.clear()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any | None = None,
    ) -> dict[str, Any]:
        if chat_id in self._forbidden:
            raise MailTelegramForbiddenError("blocked")
        if chat_id in self._api_error:
            raise MailTelegramApiError("429")
        self.sent.append((chat_id, text))
        return {"ok": True}

    async def answer_callback_query(self, *_a: Any, **_k: Any) -> None:
        return None


def _make_dispatcher(sm: Any, bot: FakeMailBot, monkeypatch: pytest.MonkeyPatch) -> Any:
    """MailDispatcherService с инъекцией фейкового бота и настройками по умолчанию."""
    from app.config import get_settings
    from app.services.mail_dispatcher_service import MailDispatcherService

    monkeypatch.setenv("MAIL_BOT_TOKEN", "123:abc")  # bot.is_configured проверяется по токену
    monkeypatch.setenv("MAIL_TG_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("MAIL_TG_NOTIFY_ALL_MESSAGES", "true")
    get_settings.cache_clear()
    svc = MailDispatcherService(sessionmaker=sm, settings=get_settings())
    svc._bot = bot  # инъекция фейкового Bot API
    return svc


async def _notifications(sm: Any) -> list[dict[str, Any]]:
    async with sm() as s:
        rows = (
            await s.execute(
                sa_text(
                    "SELECT telegram_user_id, status, attempts FROM mail_telegram_notifications "
                    "ORDER BY telegram_user_id"
                )
            )
        ).all()
    return [{"chat": int(r[0]), "status": r[1], "attempts": int(r[2])} for r in rows]


# --- Проход A: резолв получателей команды ------------------------------------
async def test_pass_a_delivers_to_team_members(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            u1 = await seed_user(s, role, telegram="u1")
            u2 = await seed_user(s, role, telegram="u2")
            team = await seed_team(s)
            await s.execute(
                sa_text("INSERT INTO user_teams (user_id, team_id) VALUES (:u1,:t),(:u2,:t)"),
                {"u1": u1.id, "u2": u2.id, "t": team.id},
            )
            await seed_link(s, telegram_user_id=101, user_id=u1.id, username="u1")
            await seed_link(s, telegram_user_id=102, user_id=u2.id, username="u2")
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
        bot = FakeMailBot()
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
        first_sent = {c for c, _ in bot.sent}
        notifs = await _notifications(sm)
        # notified_at выставлен ПОСЛЕ доставки → повторный poll не рассылает снова.
        bot.sent.clear()
        await svc.poll_once()
        resent = list(bot.sent)
    assert first_sent == {101, 102}
    assert all(n["status"] == "sent" for n in notifs)
    assert resent == []  # notified_at гейтит повторную рассылку


# --- opt-out исключает получателя --------------------------------------------
async def test_pass_a_opt_out_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            u1 = await seed_user(s, role, telegram="u1")
            u2 = await seed_user(s, role, telegram="u2")
            team = await seed_team(s)
            await s.execute(
                sa_text("INSERT INTO user_teams (user_id, team_id) VALUES (:u1,:t),(:u2,:t)"),
                {"u1": u1.id, "u2": u2.id, "t": team.id},
            )
            await seed_link(s, telegram_user_id=101, user_id=u1.id, username="u1")
            await seed_link(s, telegram_user_id=102, user_id=u2.id, username="u2")
            await seed_user_settings(s, user_id=u2.id, enabled=False)  # u2 отписан
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
        bot = FakeMailBot()
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
    assert {c for c, _ in bot.sent} == {101}  # u2 (opt-out) не получил


# --- мёртвая привязка исключается --------------------------------------------
async def test_pass_a_dead_link_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            u1 = await seed_user(s, role, telegram="u1")
            team = await seed_team(s)
            await s.execute(
                sa_text("INSERT INTO user_teams (user_id, team_id) VALUES (:u,:t)"),
                {"u": u1.id, "t": team.id},
            )
            await seed_link(s, telegram_user_id=101, user_id=u1.id, username="u1", dead_at=dt(2020))
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
        bot = FakeMailBot()
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
    assert bot.sent == []  # мёртвая привязка → не резолвится в получатели


# --- Дедуп: try_reserve → уже доставленное не пере-рассылается ----------------
async def test_dedup_existing_notification_not_resent(monkeypatch: pytest.MonkeyPatch) -> None:
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
            msg = await seed_message(s, account_id=1, uid=1, internal_date=dt())
            # Уже есть доставленная строка на эту пару (message, chat) — reserve вернёт None.
            await seed_notification(s, message_id=msg.id, telegram_user_id=101, status="sent")
            await s.commit()
        bot = FakeMailBot()
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
    # Проход A не пере-рассылает (reserve вернул None). Проход B не берёт status='sent'.
    assert bot.sent == []


# --- Проход A: перманентный сбой (403) → dead + link.dead_at ------------------
async def test_pass_a_forbidden_marks_dead(monkeypatch: pytest.MonkeyPatch) -> None:
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
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
        bot = FakeMailBot()
        bot.forbidden_for(101)
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
    notifs = await _notifications(sm)
    assert notifs[0]["status"] == "dead"
    async with sm() as s:
        dead = (
            await s.execute(
                sa_text("SELECT dead_at FROM mail_telegram_links WHERE telegram_user_id=101")
            )
        ).scalar_one()
    assert dead is not None  # привязка помечена мёртвой


# --- Проход B: транзиентный сбой → failed → recovery досылает -----------------
async def test_pass_b_recovery_redelivers_transient(monkeypatch: pytest.MonkeyPatch) -> None:
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
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
        bot = FakeMailBot()
        bot.api_error_for(101)  # первый прогон — транзиентный сбой
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
        after_first = await _notifications(sm)
        # Второй прогон — Telegram «поднялся»: recovery (проход B) досылает.
        bot.clear_errors()
        await svc.poll_once()
        after_second = await _notifications(sm)
    # A и B выполняются в одном poll_once: A → attempts=1 (failed), B (recovery в той же
    # итерации) снова пробует → attempts=2 (failed). Уведомление НЕ потеряно (не sent).
    assert after_first[0]["status"] == "failed"
    assert after_first[0]["attempts"] == 2
    assert after_second[0]["status"] == "sent"  # recovery добрал после «поднятия» Telegram
    assert 101 in {c for c, _ in bot.sent}


# --- Проход B: attempts >= max → dead, не вечный цикл -------------------------
async def test_pass_b_exhausted_attempts_dead(monkeypatch: pytest.MonkeyPatch) -> None:
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
            msg = await seed_message(s, account_id=1, uid=1, internal_date=dt(), notified_at=dt())
            # Строка уже почти исчерпала попытки (max=3): attempts=2, failed.
            await seed_notification(s, message_id=msg.id, telegram_user_id=101, status="failed")
            await s.execute(
                sa_text(
                    "UPDATE mail_telegram_notifications SET attempts=2 WHERE telegram_user_id=101"
                )
            )
            await s.commit()
        bot = FakeMailBot()
        bot.api_error_for(101)  # снова транзиентный сбой → attempts станет 3 = max → dead
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
        after = await _notifications(sm)
        # Ещё прогон: dead-строку проход B больше НЕ берёт (не вечный цикл).
        bot.sent.clear()
        await svc.poll_once()
    assert after[0]["status"] == "dead"
    assert after[0]["attempts"] == 3
    assert bot.sent == []  # dead не ретраится


# --- Проход C: guarded down-alert; штампованные (миграция) не алертят ---------
async def test_pass_c_stamped_downs_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
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
            # 7 упавших ящиков со штампом (как после миграции) → алертов быть НЕ должно.
            for i in range(1, 8):
                await seed_account(
                    s,
                    account_id=i,
                    team_id=team.id,
                    is_active=False,
                    down_alert_sent_at=dt(2026, 1, 1),
                )
            await s.commit()
        bot = FakeMailBot()
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
    assert bot.sent == []  # штампованные падения (миграция) не порождают алертов


async def test_pass_c_new_down_alerts_once(monkeypatch: pytest.MonkeyPatch) -> None:
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
            # Новое падение: is_active=false, штамп NULL → алерт РОВНО один.
            await seed_account(
                s,
                account_id=1,
                team_id=team.id,
                is_active=False,
                last_sync_error="auth failed",
                down_alert_sent_at=None,
            )
            await s.commit()
        bot = FakeMailBot()
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()
        first = list(bot.sent)
        await svc.poll_once()  # повтор без перехода — не должно слать снова
        second = list(bot.sent)
    assert len(first) == 1
    assert first[0][0] == 101
    assert "не работает" in first[0][1]
    assert second == first  # guarded: ровно один алерт на переход


async def test_pass_c_stamp_reset_on_reenable_realerts(monkeypatch: pytest.MonkeyPatch) -> None:
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
            await seed_account(
                s, account_id=1, team_id=team.id, is_active=False, down_alert_sent_at=None
            )
            await s.commit()
        bot = FakeMailBot()
        svc = _make_dispatcher(sm, bot, monkeypatch)
        await svc.poll_once()  # первый алерт
        # Симулируем re-enable→повторное падение: статус-канал сбросил штамп в NULL.
        async with sm() as s:
            await s.execute(sa_text("UPDATE mail_accounts SET down_alert_sent_at=NULL WHERE id=1"))
            await s.commit()
        await svc.poll_once()  # новое падение (штамп сброшен) → новый алерт
    assert len([1 for _ in bot.sent]) == 2  # два алерта: изначальный + после сброса
