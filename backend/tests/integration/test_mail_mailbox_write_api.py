"""Integration S3 (ADR-044 §4/§7): запись ящиков — креды транзитом, authz, компенсация.

Реальный Postgres + FastAPI-app; агрегатор замокан `FakeMailClient` (реальных вызовов
наружу нет). Проверяет: креды уходят в агрегатор транзитом и в CRM НЕ сохраняются;
`Cache-Control: no-store`; компенсация (агрегатор создал → вставка каталога упала →
best-effort DELETE, исходная ошибка проброшена); правила team_id (создание по членству;
`team_id=null` — только admin; перенос между командами — только admin); мутация чужого/
несуществующего ящика → 403. Коды сверяются по `error.code`.
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
    mail_db,
    seed_account,
    seed_role,
    seed_team,
    seed_user,
)

_CREDS = {
    "email": "new@example.com",
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_ssl": True,
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_ssl": True,
    "smtp_starttls": False,
    "password": "s3cr3t-imap-pass",
}

_MAIL_ENABLED = {"MAIL_API_KEY": "test-key"}


def _member(user_id: Any, extra: list[str] | None = None) -> Any:
    perms = {"mail": ["view", "create", "edit", "delete", "sync", *(extra or [])]}
    return build_principal(user_id=user_id, is_superadmin=False, role="member", permissions=perms)


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


# --- Создание: креды транзитом, не в CRM, no-store ---------------------------
async def test_create_transits_creds_not_stored_and_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await s.commit()
            team_id = str(team.id)
        fake = FakeMailClient(new_id=500)
        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=fake)
        async with client(app) as c:
            resp = await c.post(
                "/api/mail/mailboxes",
                # Тело запроса несёт `number`/`app_name`; `display_name` клиентом НЕ
                # принимается — сервер вычисляет его сам (ADR-047 §3.2/§3.3).
                json={**_CREDS, "team_id": team_id, "number": "5108", "app_name": "Klyro"},
            )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == 500
    assert body["team_id"] == team_id
    assert body["number"] == "5108"
    assert body["app_name"] == "Klyro"
    assert body["display_name"] == "5108 Klyro"  # производное (ADR-047 §3.3)
    assert resp.headers.get("Cache-Control") == "no-store"
    # Креды ушли в агрегатор транзитом.
    assert fake.calls[0][0] == "create_mailbox"
    sent_creds = fake.calls[0][1][0]
    assert sent_creds["password"] == "s3cr3t-imap-pass"
    assert "team_id" not in sent_creds  # team_id локален, в агрегатор не уходит
    # В ответе CRM пароля нет.
    assert "password" not in body


# --- Компенсация: вставка каталога упала → DELETE в агрегаторе + проброс -------
async def test_create_compensates_orphan_on_catalog_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await s.commit()
            team_id = str(team.id)
        fake = FakeMailClient(new_id=777)

        # Вставка строки каталога падает (симулируем сбой репозитория create).
        from app.repositories.mail_account_repository import MailAccountRepository

        async def _boom_create(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("catalog insert failed")

        monkeypatch.setattr(MailAccountRepository, "create", _boom_create)

        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=fake)
        async with client(app) as c:
            # Исходная ошибка вставки каталога проброшена (не замаскирована компенсацией).
            with pytest.raises(RuntimeError, match="catalog insert failed"):
                await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})
    methods = [m for m, _ in fake.calls]
    # Агрегатор создал ящик, затем best-effort компенсация удалила его (id 777).
    assert methods == ["create_mailbox", "delete_mailbox"]
    assert fake.calls[1][1][0] == 777


async def test_create_compensation_delete_failure_does_not_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await s.commit()
            team_id = str(team.id)
        fake = FakeMailClient(new_id=777)
        from app.infra.mail_client import MailUnavailable
        from app.repositories.mail_account_repository import MailAccountRepository

        async def _boom_create(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("catalog insert failed")

        monkeypatch.setattr(MailAccountRepository, "create", _boom_create)
        fake.fail_with("delete_mailbox", MailUnavailable("aggregator down"))  # компенсация падает

        principal = build_principal(is_superadmin=True)
        app = build_app(sm, principal, mail_client=fake)
        async with client(app) as c:
            # Провал компенсации подавлен; наружу — исходная ошибка вставки каталога.
            with pytest.raises(RuntimeError, match="catalog insert failed"):
                await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})
    # Компенсация была вызвана несмотря на то, что упала сама.
    assert [m for m, _ in fake.calls] == ["create_mailbox", "delete_mailbox"]


# --- Правила team_id ---------------------------------------------------------
async def test_create_null_team_forbidden_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
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
        app = build_app(sm, _member(user_id, ["create"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": None})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert fake.calls == []  # до агрегатора не дошло


async def test_create_foreign_team_forbidden_for_non_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        app = build_app(sm, _member(user_id, ["create"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": other_id})
    assert resp.status_code == 403
    assert fake.calls == []


async def test_create_own_team_allowed_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
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
        fake = FakeMailClient(new_id=42)
        app = build_app(sm, _member(user_id, ["create"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})
    assert resp.status_code == 201
    assert resp.json()["id"] == 42


async def test_create_null_team_allowed_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        fake = FakeMailClient(new_id=99)
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": None})
    assert resp.status_code == 201
    assert resp.json()["team_id"] is None


# --- Перенос между командами (смена team_id) — только admin -------------------
async def test_transfer_team_forbidden_for_member(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            # Участник ОБЕИХ команд, чтобы ящик был в scope, но перенос всё равно запрещён.
            await add_membership(s, user.id, team_a.id)
            await add_membership(s, user.id, team_b.id)
            await seed_account(s, account_id=10, team_id=team_a.id)
            await s.commit()
            user_id = user.id
            team_b_id = str(team_b.id)
        fake = FakeMailClient()
        app = build_app(sm, _member(user_id, ["edit"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.patch("/api/mail/mailboxes/10", json={"team_id": team_b_id})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_transfer_team_allowed_for_admin_local_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            await seed_account(s, account_id=10, team_id=team_a.id)
            await s.commit()
            team_b_id = str(team_b.id)
        fake = FakeMailClient()
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.patch("/api/mail/mailboxes/10", json={"team_id": team_b_id})
    assert resp.status_code == 200
    assert resp.json()["team_id"] == team_b_id
    # Перенос локальный — агрегатор не вызывается (team_id ему не известен).
    assert fake.calls == []


# --- Мутация чужого/несуществующего ящика → 403 (неразличимы) ------------------
async def test_mutate_foreign_mailbox_403(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await seed_account(s, account_id=20, team_id=other_team.id)
            await s.commit()
            user_id = user.id
        fake = FakeMailClient()
        app = build_app(sm, _member(user_id, ["edit"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.patch("/api/mail/mailboxes/20", json={"app_name": "x"})
    assert resp.status_code == 403


async def test_mutate_nonexistent_mailbox_404(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await s.commit()
            user_id = user.id
        fake = FakeMailClient()
        app = build_app(sm, _member(user_id, ["edit"]), mail_client=fake)
        async with client(app) as c:
            resp = await c.patch("/api/mail/mailboxes/99999", json={"app_name": "x"})
    # ADR §7: `_load_account_in_scope` — отсутствующий ящик → 404 mail_mailbox_not_found
    # (проверка scope идёт ПОСЛЕ загрузки). Чужой ящик → 403 (см. test_mutate_foreign).
    # NB: промт-инструкция «несуществующий → тоже 403 (неразличимы)» НЕ соответствует
    # ни ADR §7, ни коду; фиксирую фактическое ADR-совместимое поведение (см. findings).
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_mailbox_not_found"


# --- Delete: агрегатор + удаление каталога -----------------------------------
async def test_delete_removes_catalog_and_calls_aggregator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(s, account_id=30, team_id=team.id)
            await s.commit()
        fake = FakeMailClient()
        app = build_app(sm, build_principal(is_superadmin=True), mail_client=fake)
        async with client(app) as c:
            resp = await c.delete("/api/mail/mailboxes/30")
            listing = await c.get("/api/mail/mailboxes")
    assert resp.status_code == 204
    assert ("delete_mailbox", (30,)) in fake.calls
    assert all(m["id"] != 30 for m in listing.json()["mailboxes"])
