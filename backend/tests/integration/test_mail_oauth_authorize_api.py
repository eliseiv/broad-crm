"""Integration (ADR-045 §3): `POST /api/mail/mailboxes/oauth/authorize` — гейты + маппинг.

Реальный Postgres + FastAPI-app; агрегатор замокан `FakeMailClient`. Проверяет: гейт
`mail:create` (403 без права); авторизация команды идентична созданию ящика (`team_id=null`
не-admin → 403; чужая команда → 403; admin с несуществующей → 404; admin с `team_id=null` →
200, Вариант B); недоступность Outlook-OAuth (`MAIL_API_KEY` пуст → 503; агрегатор 404 →
503 mail_not_configured); транзиентная недоступность агрегатора → 502; успех → 200
`{authorize_url}`; `crm_state` не логируется. Коды сверяются по `error.code`.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from mail_s34_helpers import (
    FakeMailClient,
    add_membership,
    build_app,
    build_principal,
    client,
    mail_db,
    seed_role,
    seed_team,
    seed_user,
)

_MAIL_ENABLED = {"MAIL_API_KEY": "test-key"}


def _member(user_id: Any, actions: list[str]) -> Any:
    return build_principal(
        user_id=user_id,
        is_superadmin=False,
        role="member",
        permissions={"mail": actions},
    )


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    monkeypatch.setenv("MAIL_PUSH_SECRET", "authorize-state-secret")
    get_settings.cache_clear()


async def _disable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "")
    monkeypatch.setenv("MAIL_PUSH_SECRET", "authorize-state-secret")
    get_settings.cache_clear()


# --------------------------------------------------------------- успех (admin)
async def test_authorize_200_returns_authorize_url_for_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await s.commit()
            team_id = str(team.id)
        fake = FakeMailClient()
        fake.set_response(
            "authorize_oauth", {"authorize_url": "https://login.microsoftonline.com/x?a=1"}
        )
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": team_id})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"authorize_url": "https://login.microsoftonline.com/x?a=1"}
    assert resp.headers.get("Cache-Control") == "no-store"
    # crm_state ушёл транзитом в агрегатор (не пустой).
    assert fake.calls[0][0] == "authorize_oauth"
    assert isinstance(fake.calls[0][1][0], str) and fake.calls[0][1][0]


# ---------------------- Вариант B: admin может подключить без команды (team_id=null)
async def test_authorize_200_admin_null_team_variant_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        fake = FakeMailClient()
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
    assert resp.status_code == 200, resp.text
    assert "authorize_url" in resp.json()
    assert fake.calls[0][0] == "authorize_oauth"


async def test_authorize_200_own_team_for_member(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await s.commit()
            user_id = user.id
            team_id = str(my_team.id)
        fake = FakeMailClient()
        app = build_app(sm, _member(user_id, ["view", "create"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": team_id})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------- гейт mail:create → 403
async def test_authorize_403_without_create_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, user.id, team.id)
            await s.commit()
            user_id = user.id
            team_id = str(team.id)
        fake = FakeMailClient()
        # Только view — нет mail:create.
        app = build_app(sm, _member(user_id, ["view"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": team_id})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert fake.calls == []  # до агрегатора не дошло


# ---------------------------------------------- team_id=null не-admin → 403
async def test_authorize_403_null_team_for_member(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            team = await seed_team(s)
            await add_membership(s, user.id, team.id)
            await s.commit()
            user_id = user.id
        fake = FakeMailClient()
        app = build_app(sm, _member(user_id, ["view", "create"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert fake.calls == []


# ---------------------------------------------- чужая команда не-admin → 403
async def test_authorize_403_foreign_team_for_member(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await s.commit()
            user_id = user.id
            other_id = str(other_team.id)
        fake = FakeMailClient()
        app = build_app(sm, _member(user_id, ["view", "create"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": other_id})
    assert resp.status_code == 403
    assert fake.calls == []


# ------------------------------------------ admin несуществующая команда → 404
async def test_authorize_404_nonexistent_team_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        fake = FakeMailClient()
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post(
                "/api/mail/mailboxes/oauth/authorize",
                json={"team_id": "00000000-0000-0000-0000-0000000000ff"},
            )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "team_not_found"
    assert fake.calls == []


# ------------------------------- MAIL_API_KEY пуст → 503 mail_not_configured
async def test_authorize_503_when_mail_api_key_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    await _disable_mail(monkeypatch)
    async with mail_db() as sm:
        fake = FakeMailClient()
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "mail_not_configured"
    assert fake.calls == []  # гейт _ensure_configured до вызова агрегатора


# --------------------- агрегатор вернул 404 → 503 mail_not_configured (§3)
async def test_authorize_503_when_aggregator_404(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        from app.infra.mail_client import MailRejected

        fake = FakeMailClient()
        fake.fail_with("authorize_oauth", MailRejected(404))
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "mail_not_configured"


# ------------------------ транзиентная недоступность агрегатора → 502
async def test_authorize_502_when_aggregator_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        from app.infra.mail_client import MailUnavailable

        fake = FakeMailClient()
        fake.fail_with("authorize_oauth", MailUnavailable("connect error"))
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


# ------------------ прочий 4xx агрегатора при валидном crm_state → 502
async def test_authorize_502_when_aggregator_unexpected_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        from app.infra.mail_client import MailRejected

        fake = FakeMailClient()
        fake.fail_with("authorize_oauth", MailRejected(400))
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


# ------------------ агрегатор без authorize_url в ответе → 502 (регресс контракта)
async def test_authorize_502_when_url_missing_in_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        fake = FakeMailClient()
        fake.set_response("authorize_oauth", {"state": "s"})  # нет authorize_url
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


# ---------------------------------- безопасность: crm_state не попадает в логи
async def test_authorize_crm_state_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        fake = FakeMailClient()
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        with structlog.testing.capture_logs() as logs:
            async with client(app) as c:
                resp = await c.post("/api/mail/mailboxes/oauth/authorize", json={"team_id": None})
        assert resp.status_code == 200, resp.text
        crm_state = fake.calls[0][1][0]
        # Значение подписанного токена не должно фигурировать ни в одном событии лога.
        for event in logs:
            for value in event.values():
                assert crm_state not in str(value)
