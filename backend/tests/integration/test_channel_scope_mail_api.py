"""Per-channel scope почты + контекст ящика в ленте (ADR-055, ADR-056) — реальный Postgres.

Перечень кейсов нормативен (06-testing-strategy.md §«Per-channel scope команд» и
§«`MailAccountRef`»); пройден ПОЭЛЕМЕНТНО, по кейсу на каждый пункт:

- **union-семантика** — не-админ с доп-командой видит ленту и каталог ОБЕИХ команд;
  объект доп-команды доступен на мутацию (доп-команда = полноценная команда канала, §4);
- **«Без команды» (`mail_includes_unassigned`)** — бесхозный ящик виден и мутируется; без
  флага — в чтении отсутствует (НЕ 403), мутация → 403;
- **`no_team`** — только бесхозные; `team_id` + `no_team=true` → 400 validation_error;
  без флага → пустая страница (не 403);
- **неразворот ADR-044 §4** — создание ящика с `team_id=null` не-админом → 403 ДАЖЕ при
  флаге; смена `team_id` ящика не-админом (в т.ч. в свою доп-команду) → 403;
- **`GET /api/auth/me`** — не-админ: эффективный scope (базовые ∪ добавка); актор
  admin-уровня: ВСЕ команды системы (не `[]`) + флаги `true` (§5.1);
- **`MailAccountRef` (ADR-056)** — `number`/`app_name`/`team`; бесхозный ящик → `team=null`
  и письмо НЕ пропадает из ленты (LEFT JOIN); N+1 отсутствует (число SQL-запросов на
  страницу ленты не растёт с числом писем).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from mail_s34_helpers import (
    FakeMailClient,
    add_extra_team,
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
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_MAIL_FULL = {"mail": ["view", "create", "edit", "delete", "sync", "tags"]}

_CREATE_BODY: dict[str, Any] = {
    "email": "new@example.com",
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_ssl": True,
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_ssl": True,
    "smtp_starttls": False,
    "password": "app-code",
}


@pytest.fixture(autouse=True)
def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включает mail-интеграцию: без `MAIL_API_KEY` пути записи отдают 503 mail_not_configured.

    Фикстура автоюзная и локальная для модуля — глобальное состояние (`get_settings`
    LRU-кэш) сбрасывается здесь же, чтобы соседние тесты не наследовали ключ.
    """
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


def _create_body(**overrides: Any) -> dict[str, Any]:
    body = dict(_CREATE_BODY)
    body.update(overrides)
    return body


def _non_admin(user_id: uuid.UUID, **flags: bool) -> Any:
    """Принципал не-админа с ПОЛНЫМИ правами почты (но не полным каталогом) — §3."""
    return build_principal(
        user_id=user_id,
        is_superadmin=False,
        role="Оператор",
        permissions=_MAIL_FULL,
        **flags,
    )


# --- union-семантика (базовые ∪ доп-команды) ---------------------------------


async def test_union_scope_feed_and_catalog_include_base_and_extra_team() -> None:
    """Не-админ с доп-командой B видит ленту и каталог ОБЕИХ команд (A ∪ B), §3."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s, name="Альфа")
            team_b = await seed_team(s, name="Бета")
            team_c = await seed_team(s, name="Гамма")  # чужая — вне scope
            await add_membership(s, user.id, team_a.id)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await seed_account(s, account_id=1, team_id=team_a.id)
            await seed_account(s, account_id=2, team_id=team_b.id)
            await seed_account(s, account_id=3, team_id=team_c.id)
            for acc in (1, 2, 3):
                await seed_message(s, account_id=acc, uid=acc, internal_date=dt(2026, 6, acc))
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            feed = await c.get("/api/mail/messages")
            catalog = await c.get("/api/mail/mailboxes")

    assert feed.status_code == 200, feed.text
    assert {m["mail_account"]["id"] for m in feed.json()["messages"]} == {1, 2}
    assert catalog.status_code == 200
    assert {m["id"] for m in catalog.json()["mailboxes"]} == {1, 2}


async def test_extra_team_mailbox_is_mutable_by_non_admin() -> None:
    """Доп-команда — ПОЛНОЦЕННАЯ команда канала (§4): ящик команды B правится (200)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await seed_account(s, account_id=7, team_id=team_b.id)
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            patched = await c.patch("/api/mail/mailboxes/7", json={"is_active": False})
            synced = await c.post("/api/mail/mailboxes/7/sync")

    assert patched.status_code == 200, patched.text
    assert patched.json()["is_active"] is False
    assert synced.status_code in (200, 202), synced.text


async def test_sms_extra_team_does_not_leak_into_mail_scope() -> None:
    """Каналы независимы: доп-команда канала `sms` НЕ расширяет scope почты (§1)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            await add_extra_team(s, user.id, "sms", team_b.id)  # добавка ДРУГОГО канала
            await seed_account(s, account_id=5, team_id=team_b.id)
            await seed_message(s, account_id=5, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            feed = await c.get("/api/mail/messages")
            mutation = await c.patch("/api/mail/mailboxes/5", json={"is_active": False})

    assert feed.json()["messages"] == []
    assert mutation.status_code == 403
    assert mutation.json()["error"]["code"] == "forbidden"


# --- Флаг «Без команды» (mail_includes_unassigned) ----------------------------


async def test_includes_unassigned_orphan_mailbox_visible_and_mutable() -> None:
    """С флагом «Без команды»: бесхозный ящик виден в ленте/каталоге и МУТИРУЕТСЯ (§3/§5.3)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await seed_account(s, account_id=1, team_id=team_a.id)
            await seed_account(s, account_id=2, team_id=None)  # бесхозный
            await seed_message(s, account_id=2, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id = user.id

        app = build_app(
            sm,
            _non_admin(user_id, mail_includes_unassigned=True),
            mail_client=FakeMailClient(),
        )
        async with client(app) as c:
            feed = await c.get("/api/mail/messages")
            catalog = await c.get("/api/mail/mailboxes")
            patched = await c.patch("/api/mail/mailboxes/2", json={"is_active": False})
            deleted = await c.delete("/api/mail/mailboxes/2")

    # Каталог обязан включать бесхозные (§5.3: иначе клиентский фильтр «Без команды»
    # показал бы пустоту у пользователя, который эти ящики вправе править).
    assert {m["id"] for m in catalog.json()["mailboxes"]} == {1, 2}
    assert [m["mail_account"]["id"] for m in feed.json()["messages"]] == [2]
    assert patched.status_code == 200, patched.text
    assert deleted.status_code == 204, deleted.text


async def test_without_flag_orphan_mailbox_absent_in_read_and_403_on_mutation() -> None:
    """Без флага: бесхозный ящик в чтении ОТСУТСТВУЕТ (не 403), мутация → 403 (§3)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await seed_account(s, account_id=1, team_id=team_a.id)
            await seed_account(s, account_id=2, team_id=None)
            await seed_message(s, account_id=2, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            feed = await c.get("/api/mail/messages")
            catalog = await c.get("/api/mail/mailboxes")
            patched = await c.patch("/api/mail/mailboxes/2", json={"is_active": False})
            deleted = await c.delete("/api/mail/mailboxes/2")

    # Чтение — анти-энумерация: 200 с пустотой, НЕ 403.
    assert feed.status_code == 200
    assert feed.json()["messages"] == []
    assert {m["id"] for m in catalog.json()["mailboxes"]} == {1}
    assert patched.status_code == 403
    assert patched.json()["error"]["code"] == "forbidden"
    assert deleted.status_code == 403


# --- Фильтр `no_team` ленты (§5.3) -------------------------------------------


async def test_no_team_filter_returns_only_orphan_mailbox_messages() -> None:
    """`no_team=true` → ТОЛЬКО письма ящиков без команды (§5.3)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await seed_account(s, account_id=1, team_id=team_a.id)
            await seed_account(s, account_id=2, team_id=None)
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 6, 1))
            await seed_message(s, account_id=2, uid=2, internal_date=dt(2026, 6, 2))
            await s.commit()
            user_id = user.id

        app = build_app(
            sm,
            _non_admin(user_id, mail_includes_unassigned=True),
            mail_client=FakeMailClient(),
        )
        async with client(app) as c:
            resp = await c.get("/api/mail/messages", params={"no_team": "true"})

    assert resp.status_code == 200, resp.text
    assert [m["mail_account"]["id"] for m in resp.json()["messages"]] == [2]


async def test_no_team_with_team_id_is_400_validation_error() -> None:
    """`team_id` и `no_team=true` взаимоисключающи → 400 validation_error (§5.3)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await s.commit()
            user_id, team_id = user.id, team_a.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get(
                "/api/mail/messages",
                params={"team_id": str(team_id), "no_team": "true"},
            )

    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


async def test_no_team_without_flag_returns_empty_page_not_403() -> None:
    """`no_team=true` у не-админа БЕЗ флага → пустая страница (анти-энумерация, не 403)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await seed_account(s, account_id=1, team_id=team_a.id)
            await seed_account(s, account_id=2, team_id=None)
            await seed_message(s, account_id=2, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.get("/api/mail/messages", params={"no_team": "true"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["messages"] == []


# --- Неразворот ADR-044 §4 (создание/перенос ящика — admin-only) --------------


async def test_create_mailbox_with_null_team_is_403_for_non_admin_even_with_flag() -> None:
    """ADR-044 §4 НЕ разворачивается: `team_id=null` при создании → 403 ДАЖЕ с флагом (§3).

    Флаг «Без команды» даёт работу с СУЩЕСТВУЮЩИМИ бесхозными ящиками, но НЕ право
    создавать новые вне командной модели.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await s.commit()
            user_id = user.id

        fake = FakeMailClient()
        app = build_app(
            sm,
            _non_admin(user_id, mail_includes_unassigned=True),
            mail_client=fake,
        )
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json=_create_body(team_id=None))

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"
    # Отказ до сети: агрегатор не тронут (ящика-сироты в нём не появилось).
    assert [name for name, _ in fake.calls] == []


async def test_patch_mailbox_team_change_is_403_for_non_admin_even_into_own_extra_team() -> None:
    """Перенос ящика — ТОЛЬКО admin-уровень (§3): смена `team_id` не-админом → 403.

    В т.ч. в СВОЮ доп-команду: иначе доп-команды стали бы обходным путём выноса ящика
    из чужой команды.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await seed_account(s, account_id=1, team_id=team_a.id)
            await s.commit()
            user_id, target = user.id, team_b.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.patch("/api/mail/mailboxes/1", json={"team_id": str(target)})

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


async def test_create_mailbox_in_extra_team_succeeds_for_non_admin() -> None:
    """Не-админ создаёт ящик в СВОЕЙ доп-команде → 201 (`team_ids` включает добавку, §3)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await s.commit()
            user_id, target = user.id, team_b.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient(new_id=42))
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json=_create_body(team_id=str(target)))

    assert resp.status_code == 201, resp.text
    assert resp.json()["team_id"] == str(target)


async def test_create_mailbox_in_foreign_team_is_403_for_non_admin() -> None:
    """Не-админ создаёт ящик в ЧУЖОЙ команде (вне scope) → 403 (§3)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s)
            foreign = await seed_team(s)
            await add_membership(s, user.id, team_a.id)
            await s.commit()
            user_id, target = user.id, foreign.id

        app = build_app(sm, _non_admin(user_id), mail_client=FakeMailClient())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json=_create_body(team_id=str(target)))

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


# --- GET /api/auth/me: scope каналов (§5.1) ----------------------------------


async def test_auth_me_non_admin_returns_effective_channel_scope() -> None:
    """Не-админ: `mail_teams` = ЭФФЕКТИВНЫЙ scope (базовые ∪ добавка), сортировка по name."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team_a = await seed_team(s, name="Ярославль")
            team_b = await seed_team(s, name="Астрахань")
            await seed_team(s, name="Чужая")
            await add_membership(s, user.id, team_a.id)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id, mail_includes_unassigned=True))
        async with client(app) as c:
            resp = await c.get("/api/auth/me")

    body = resp.json()
    assert resp.status_code == 200, resp.text
    # Базовые ∪ добавка (объединение, НЕ только добавка), отсортировано по name (ru, ci).
    assert [t["name"] for t in body["mail_teams"]] == ["Астрахань", "Ярославль"]
    assert body["mail_includes_unassigned"] is True
    # Канал `sms` независим: добавок нет ⇒ только базовая команда, флаг false.
    assert [t["name"] for t in body["sms_teams"]] == ["Ярославль"]
    assert body["sms_includes_unassigned"] is False
    assert body["sees_all_mail_teams"] is False


async def test_auth_me_admin_level_returns_all_system_teams_not_empty_list() -> None:
    """Актор admin-уровня: `*_teams` = ВСЕ команды системы (НЕ `[]`), флаги `true` (§5.1).

    Регрессия дефекта редакции 1 ADR-055: `[]` оставил бы фильтр «Команда» в Mini App
    у admin-уровня пустым (`GET /api/teams` оттуда запрещён).
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            await seed_team(s, name="Бета")
            await seed_team(s, name="Альфа")
            await s.commit()
            user_id = user.id

        # БД-пользователь с ПОЛНЫМ каталогом прав (не супер-админ) — admin-уровень.
        principal = build_principal(user_id=user_id, is_superadmin=False, role="Админ")
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/auth/me")

    body = resp.json()
    assert body["sees_all_mail_teams"] is True
    assert body["sees_all_sms_teams"] is True
    assert [t["name"] for t in body["mail_teams"]] == ["Альфа", "Бета"]
    assert [t["name"] for t in body["sms_teams"]] == ["Альфа", "Бета"]
    assert body["mail_includes_unassigned"] is True
    assert body["sms_includes_unassigned"] is True


# --- ADR-056: MailAccountRef несёт контекст ящика ----------------------------


async def test_mail_account_ref_carries_number_app_name_and_team() -> None:
    """`MailAccountRef` = `{id, email, display_name, number, app_name, team}` (ADR-056 §1)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team = await seed_team(s, name="Команда Ивана")
            await add_membership(s, user.id, team.id)
            await seed_account(
                s,
                account_id=3,
                email="inbox@example.com",
                number="5108",
                app_name="Klyro Forge",
                display_name="5108 Klyro Forge",
                team_id=team.id,
            )
            await seed_message(s, account_id=3, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id, team_id, team_name = user.id, team.id, team.name

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.get("/api/mail/messages")

    ref = resp.json()["messages"][0]["mail_account"]
    assert ref["id"] == 3
    assert ref["email"] == "inbox@example.com"
    assert ref["display_name"] == "5108 Klyro Forge"
    assert ref["number"] == "5108"
    assert ref["app_name"] == "Klyro Forge"
    assert ref["team"] == {"id": str(team_id), "name": team_name}


async def test_orphan_mailbox_message_stays_in_feed_with_team_null() -> None:
    """Ящик без команды → `team: null`, письмо НЕ пропадает из ленты (LEFT, не INNER JOIN)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            await seed_account(s, account_id=9, team_id=None, number=None, app_name=None)
            await seed_message(s, account_id=9, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id, mail_includes_unassigned=True))
        async with client(app) as c:
            resp = await c.get("/api/mail/messages")

    messages = resp.json()["messages"]
    assert len(messages) == 1  # INNER JOIN молча потерял бы это письмо
    ref = messages[0]["mail_account"]
    assert ref["team"] is None
    assert ref["number"] is None
    assert ref["app_name"] is None


async def test_mail_feed_has_no_n_plus_1_on_account_context() -> None:
    """N+1 запрещён (ADR-056 §1): число SQL-запросов не растёт с размером страницы.

    Считаются реальные обращения к БД (`before_cursor_execute`) на ОДИН запрос ленты.
    Страница из 1 письма (1 ящик) и страница из 20 писем (20 РАЗНЫХ ящиков) обязаны
    стоить одинаковое число запросов: контекст ящика подтягивается ТЕМ ЖЕ батчем
    (`get_many_with_team` — LEFT JOIN `teams`), а не запросом на письмо.
    """

    async def _count_queries(
        sm: async_sessionmaker[AsyncSession], user_id: uuid.UUID, limit: int
    ) -> int:
        counter = {"n": 0}

        def _on_execute(*_args: Any, **_kwargs: Any) -> None:
            counter["n"] += 1

        engine = sm.kw["bind"].sync_engine
        event.listen(engine, "before_cursor_execute", _on_execute)
        try:
            app = build_app(sm, _non_admin(user_id))
            async with client(app) as c:
                resp = await c.get("/api/mail/messages", params={"limit": limit})
            assert resp.status_code == 200, resp.text
            assert len(resp.json()["messages"]) == limit
        finally:
            event.remove(engine, "before_cursor_execute", _on_execute)
        return counter["n"]

    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            # 20 РАЗНЫХ ящиков (каждый со своей командой) и по письму на каждый: при N+1
            # число запросов росло бы линейно с числом писем/ящиков страницы.
            for i in range(1, 21):
                team = await seed_team(s, name=f"Команда {i:02d}")
                await add_membership(s, user.id, team.id)
                await seed_account(s, account_id=i, team_id=team.id, number=str(i))
                await seed_message(s, account_id=i, uid=i, internal_date=dt(2026, 6, 1, i % 23))
            await s.commit()
            user_id = user.id

        one = await _count_queries(sm, user_id, 1)
        twenty = await _count_queries(sm, user_id, 20)

    assert twenty == one, (
        f"N+1: страница из 20 писем стоила {twenty} запросов против {one} на 1 письмо — "
        "контекст ящика (number/app_name/team) обязан приходить одним батчем"
    )


# --- Пустой scope: пустая страница БЕЗ выборки (§3) --------------------------


@pytest.mark.parametrize("path", ["/api/mail/messages", "/api/mail/mailboxes"])
async def test_empty_scope_without_flag_returns_empty_read(path: str) -> None:
    """Не-админ без команд И без флага → пустое чтение (не 403, анти-энумерация)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions=_MAIL_FULL)
            user = await seed_user(s, role)
            team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_account(s, account_id=2, team_id=None)
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id = user.id

        app = build_app(sm, _non_admin(user_id))
        async with client(app) as c:
            resp = await c.get(path)

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("messages", body.get("mailboxes")) == []
