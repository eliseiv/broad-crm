"""Integration (ADR-047 §3): поля ящика «Номер»/«Приложение» + БЕЛЫЙ СПИСОК наружу.

Реальный Postgres + FastAPI-app; агрегатор замокан `FakeMailClient` (наружу ничего не
уходит). Проверяет нормативы ADR-047:

- §3.2 — `display_name` в теле запроса НЕ принимается (клиент шлёт `number`/`app_name`);
- §3.3 — `display_name` ПРОИЗВОДНОЕ: сервер пересчитывает его при каждом create/update;
- §3.4 — **исходящий payload в агрегатор строится БЕЛЫМ СПИСКОМ**: `number`/`app_name`
  наружу НЕ уходят НИКОГДА — ни в `create_mailbox`, ни в `update_mailbox`; при `PATCH`,
  меняющем имя, наружу уходит ПЕРЕСЧИТАННЫЙ `display_name` (ключ белого списка);
  `team_id` наружу не уходит (перенос — локальный UPDATE без сетевого вызова).
"""

from __future__ import annotations

from typing import Any

import pytest
from mail_s34_helpers import (
    FakeMailClient,
    build_app,
    build_principal,
    client,
    mail_db,
    seed_account,
    seed_team,
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


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


def _admin(sm: Any, fake: FakeMailClient) -> Any:
    return build_app(sm, build_principal(is_superadmin=True), mail_client=fake)


def _sent(fake: FakeMailClient, method: str) -> dict[str, Any]:
    """Payload последнего вызова агрегатора `method`."""
    calls = [c for c in fake.calls if c[0] == method]
    assert calls, f"агрегатор не вызывался методом {method}"
    args = calls[-1][1]
    payload = args[-1]
    assert isinstance(payload, dict)
    return payload


# ---------------------------------------------- CREATE: белый список + производное имя
async def test_create_does_not_leak_number_and_app_name_to_aggregator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3.4: `number`/`app_name` наружу НЕ уходят; уходит вычисленный `display_name`."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await s.commit()
            team_id = str(team.id)
        fake = FakeMailClient(new_id=610)
        async with client(_admin(sm, fake)) as c:
            resp = await c.post(
                "/api/mail/mailboxes",
                json={
                    **_CREDS,
                    "team_id": team_id,
                    "number": "5108",
                    "app_name": "Klyro Forge (Codex)",
                },
            )
    assert resp.status_code == 201, resp.text
    payload = _sent(fake, "create_mailbox")
    assert "number" not in payload
    assert "app_name" not in payload
    assert "team_id" not in payload
    assert payload["display_name"] == "5108 Klyro Forge (Codex)"


async def test_create_ignores_client_supplied_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3.2: `display_name` клиентом не принимается — сервер считает его сам из number/app_name."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await s.commit()
            team_id = str(team.id)
        fake = FakeMailClient(new_id=611)
        async with client(_admin(sm, fake)) as c:
            resp = await c.post(
                "/api/mail/mailboxes",
                json={
                    **_CREDS,
                    "team_id": team_id,
                    "number": "42",
                    "app_name": "App",
                    "display_name": "ПОДСУНУТОЕ ИМЯ",  # не поле схемы → игнорируется
                },
            )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["display_name"] == "42 App"  # производное, а не то, что прислал клиент
    assert _sent(fake, "create_mailbox")["display_name"] == "42 App"


async def test_create_without_name_fields_sends_null_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обе части пусты → `display_name = None` (§3.3), поля опциональны."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await s.commit()
            team_id = str(team.id)
        fake = FakeMailClient(new_id=612)
        async with client(_admin(sm, fake)) as c:
            resp = await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["number"] is None
    assert body["app_name"] is None
    assert body["display_name"] is None
    assert _sent(fake, "create_mailbox")["display_name"] is None


# ---------------------------------------------- PATCH: белый список + пересчёт имени
async def test_patch_number_recomputes_display_name_and_hides_new_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3.4: при PATCH наружу уходит ПЕРЕСЧИТАННЫЙ `display_name`; number/app_name — нет.

    Пересчёт — из ЭФФЕКТИВНЫХ значений: присланное поле + текущее из БД.
    """
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(
                s,
                account_id=620,
                team_id=team.id,
                number="5108",
                app_name="Klyro",
                display_name="5108 Klyro",
            )
            await s.commit()
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.patch("/api/mail/mailboxes/620", json={"number": "777"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["number"] == "777"
    assert body["app_name"] == "Klyro"  # не тронуто (presence-семантика)
    assert body["display_name"] == "777 Klyro"  # пересчитано из эффективных значений

    payload = _sent(fake, "update_mailbox")
    assert "number" not in payload
    assert "app_name" not in payload
    assert payload["display_name"] == "777 Klyro"


async def test_patch_app_name_to_null_recomputes_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(
                s,
                account_id=621,
                team_id=team.id,
                number="5108",
                app_name="Klyro",
                display_name="5108 Klyro",
            )
            await s.commit()
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.patch("/api/mail/mailboxes/621", json={"app_name": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "5108"
    assert _sent(fake, "update_mailbox")["display_name"] == "5108"


async def test_patch_team_id_only_makes_no_aggregator_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`team_id` — локальный UPDATE: сетевого вызова нет, имя не пересчитывается наружу."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            await seed_account(
                s,
                account_id=622,
                team_id=team_a.id,
                number="5108",
                app_name="Klyro",
                display_name="5108 Klyro",
            )
            await s.commit()
            team_b_id = str(team_b.id)
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.patch("/api/mail/mailboxes/622", json={"team_id": team_b_id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["team_id"] == team_b_id
    assert resp.json()["display_name"] == "5108 Klyro"  # имя не менялось
    assert [c[0] for c in fake.calls if c[0] == "update_mailbox"] == []


async def test_patch_ignores_client_supplied_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3.2: `display_name` в теле PATCH не принимается → имя не меняется и наружу не уходит."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(
                s,
                account_id=623,
                team_id=team.id,
                number="5108",
                app_name="Klyro",
                display_name="5108 Klyro",
            )
            await s.commit()
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.patch("/api/mail/mailboxes/623", json={"display_name": "ПОДСУНУТОЕ"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] == "5108 Klyro"  # не изменилось
    # Поле не в схеме → fields_set пуст → аггрегаторный payload пуст → вызова нет.
    assert [c[0] for c in fake.calls if c[0] == "update_mailbox"] == []


# ---------------------------------------------- ответ содержит новые поля (§3.2)
async def test_list_mailboxes_response_carries_number_and_app_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(
                s,
                account_id=630,
                team_id=team.id,
                number="173, 57, 104",
                app_name=None,
                display_name="173, 57, 104",
            )
            await s.commit()
        fake = FakeMailClient()
        async with client(_admin(sm, fake)) as c:
            resp = await c.get("/api/mail/mailboxes")
    assert resp.status_code == 200, resp.text
    box = next(m for m in resp.json()["mailboxes"] if m["id"] == 630)
    assert box["number"] == "173, 57, 104"
    assert box["app_name"] is None
    assert box["display_name"] == "173, 57, 104"
