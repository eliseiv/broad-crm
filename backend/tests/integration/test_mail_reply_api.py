"""Integration S3 (ADR-044 §8): reply — нормы валидации, дефолты, маппинг кодов, scope.

FastAPI-app + реальный Postgres; SMTP-отправка делегируется агрегатору (замокан).
Нормы §8: пустое тело/> 1 MiB/невалидный e-mail/> 100 адресов/subject > 998 → 422;
явный `to: []` без cc → 422 БЕЗ вызова агрегатора; `to: []` + непустой cc → проходит;
409/502 от агрегатора маппятся; reply чужого письма → 404 mail_message_not_found
(маскировка). Коды по `error.code`.
"""

from __future__ import annotations

from typing import Any

import pytest
from mail_s34_helpers import (
    FakeMailClient,
    add_membership,
    build_app,
    build_principal,
    client,
    dt,
    mail_db,
    seed_account,
    seed_message,
    seed_role,
    seed_team,
    seed_user,
)

_MAIL_ENABLED = {"MAIL_API_KEY": "test-key"}


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


async def _seed_one_message(sm: Any, *, from_addr: str = "orig@example.com") -> None:
    async with sm() as s:
        team = await seed_team(s)
        await seed_account(s, account_id=1, team_id=team.id)
        await seed_message(
            s,
            account_id=1,
            uid=1,
            internal_date=dt(),
            subject="Исходная",
            from_addr=from_addr,
            message_id_header="<orig@example.com>",
        )
        await s.commit()


def _admin(sm: Any, fake: Any) -> Any:
    return build_app(sm, build_principal(is_superadmin=True), mail_client=fake)


# --- Нормы 422 ---------------------------------------------------------------
async def test_empty_body_422_no_aggregator_call(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "   "})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"
    assert fake.calls == []  # до агрегатора не дошло


async def test_body_over_1mib_422(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post(
                "/api/mail/messages/1/reply", json={"body": "a" * (1024 * 1024 + 1)}
            )
    assert resp.status_code == 422
    assert fake.calls == []


async def test_invalid_email_422(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post(
                "/api/mail/messages/1/reply", json={"to": ["not-an-email"], "body": "hi"}
            )
    assert resp.status_code == 422
    assert fake.calls == []


async def test_too_many_recipients_422(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        fake = FakeMailClient()
        many = [f"user{i}@example.com" for i in range(101)]
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"to": many, "body": "hi"})
    assert resp.status_code == 422
    assert fake.calls == []


async def test_subject_over_998_422(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post(
                "/api/mail/messages/1/reply",
                json={"to": ["a@b.co"], "subject": "s" * 999, "body": "hi"},
            )
    assert resp.status_code == 422
    assert fake.calls == []


# --- to:[] без cc → 422 без вызова; to:[] + cc → проходит --------------------
async def test_explicit_empty_to_without_cc_422_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"to": [], "body": "hi"})
    assert resp.status_code == 422
    assert fake.calls == []  # ни одного получателя — агрегатор не вызывается


async def test_empty_to_with_cc_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post(
                "/api/mail/messages/1/reply",
                json={"to": [], "cc": ["cc@example.com"], "body": "hi"},
            )
    assert resp.status_code == 200
    assert fake.calls[0][0] == "send_message"
    sent = fake.calls[0][1][1]
    assert sent["to"] == []
    assert sent["cc"] == ["cc@example.com"]


# --- Дефолты: to → [оригинал.from], subject → "Re: ..." ----------------------
async def test_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm, from_addr="boss@example.com")
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "ответ"})
    assert resp.status_code == 200
    sent = fake.calls[0][1][1]
    assert sent["to"] == ["boss@example.com"]  # дефолт — from исходного
    assert sent["subject"] == "Re: Исходная"
    assert sent["in_reply_to"] == "<orig@example.com>"


# --- Маппинг кодов агрегатора ------------------------------------------------
async def test_aggregator_409_maps_to_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        from app.infra.mail_client import MailRejected

        fake = FakeMailClient()
        fake.fail_with("send_message", MailRejected(409))
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "hi"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "mail_conflict"


async def test_aggregator_unavailable_maps_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        await _seed_one_message(sm)
        from app.infra.mail_client import MailUnavailable

        fake = FakeMailClient()
        fake.fail_with("send_message", MailUnavailable("down"))
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "hi"})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


# --- Reply чужого/несуществующего письма → 404 (маскировка) -------------------
async def test_reply_foreign_message_404_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await seed_account(s, account_id=1, team_id=other_team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id
        fake = FakeMailClient()
        principal = build_principal(
            user_id=user_id, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal, mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "hi"})
    # Чужое письмо неотличимо от несуществующего → 404 mail_message_not_found.
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_message_not_found"
    assert fake.calls == []


async def test_reply_nonexistent_message_404(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/messages/424242/reply", json={"body": "hi"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_message_not_found"
