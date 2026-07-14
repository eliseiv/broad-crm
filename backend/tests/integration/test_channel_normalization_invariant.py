"""Инвариант нормализации доп-команд (ADR-055 §2.3) — SECURITY, реальный Postgres.

**Инвариант:** в `user_channel_teams` НЕ хранятся команды, входящие в `user_teams` того же
пользователя. Он обязан держаться на КАЖДОМ из ДВУХ путей записи в `user_teams` (§2.3):

| # | Путь | Здесь покрыт |
|---|------|--------------|
| 1 | **Users CRUD** (`POST`/`PATCH /api/users`, поле `team_ids`) | `test_users_crud_*` |
| 2 | **Teams CRUD** (`POST`/`PATCH /api/teams`, поле `member_ids`) | `test_teams_crud_*` |

**Почему путь 2 — отдельный обязательный SECURITY-кейс** (§2.3, разбор): без него
достижима последовательность «(а) админ даёт `X` доп-команду `B` → (б) добавляет `X` в `B`
участником на `/teams` (сервис users НЕ вызывался) → (в) исключает `X` из `B`» ⇒ строка
`user_teams` удалена, а **строка добавки осталась** ⇒ `X` СОХРАНЯЕТ доступ к почте/СМС
команды `B` после исключения из неё. Гарантия «снял в основном блоке → потерял доступ в
обоих каналах» обязана быть path-independent, а не зависеть от страницы, где админ правил
членство. Проверяется ФАКТИЧЕСКИМ доступом (лента пуста + мутация → 403), а не только
отсутствием строки в БД.

Оба канала (`mail`, `sms`) покрыты ОТДЕЛЬНО: «работает почта» за СМС не засчитывается.
"""

from __future__ import annotations

import uuid
from typing import Any

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
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_MAIL_FULL = {"mail": ["view", "create", "edit", "delete", "sync", "tags"]}


def _admin() -> Any:
    """Супер-админ — `require_admin` на users/teams CRUD (гейт §5.2 не менялся)."""
    return build_principal(is_superadmin=True)


def _operator(user_id: uuid.UUID) -> Any:
    """Не-админ с полными правами почты — под ним проверяется ФАКТИЧЕСКИЙ доступ."""
    return build_principal(
        user_id=user_id, is_superadmin=False, role="Оператор", permissions=_MAIL_FULL
    )


async def _extras(
    sm: async_sessionmaker[AsyncSession], user_id: uuid.UUID, channel: str
) -> set[uuid.UUID]:
    """Строки `user_channel_teams` пользователя по каналу — прямо из БД (не через API)."""
    async with sm() as s:
        rows = await s.execute(
            sa_text("SELECT team_id FROM user_channel_teams WHERE user_id = :u AND channel = :c"),
            {"u": user_id, "c": channel},
        )
        return {row[0] for row in rows.all()}


async def _seed_role_for_users(session: AsyncSession) -> Any:
    return await seed_role(session, permissions=_MAIL_FULL)


# --- Путь 1: users CRUD (§2.3, таблица путей записи) --------------------------


async def test_users_crud_create_subtracts_base_team_from_extras_and_is_not_422() -> None:
    """`POST /api/users`: базовая команда, присланная в добавке, НЕ сохраняется и НЕ 422 (§2.3).

    «Присланная лишняя базовая команда — не ошибка, просто не сохраняется»: сервис вычитает
    `team_ids` из `*_extra_team_ids`. Ответ несёт ТОЛЬКО добавку (§5.2).
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            team_a = await seed_team(s, name="Альфа")
            team_b = await seed_team(s, name="Бета")
            await s.commit()
            role_id, a, b = role.id, team_a.id, team_b.id

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.post(
                "/api/users",
                json={
                    "username": "Никита",
                    "role_id": str(role_id),
                    "team_ids": [str(a)],
                    # A — базовая, прислана и в добавке: вычитается, а не 422.
                    "mail_extra_team_ids": [str(a), str(b)],
                    "sms_extra_team_ids": [str(a)],
                    "mail_extra_includes_unassigned": True,
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        user_id = uuid.UUID(body["id"])

        # Ответ несёт ТОЛЬКО добавку — БЕЗ базовых команд (§5.2).
        assert [t["id"] for t in body["mail_extra_teams"]] == [str(b)]
        assert body["sms_extra_teams"] == []  # осталась только базовая → добавка пуста
        assert body["teams"][0]["id"] == str(a)
        assert body["mail_extra_includes_unassigned"] is True
        assert body["sms_extra_includes_unassigned"] is False

        # В БД хранится ровно разность (инвариант §2.3).
        assert await _extras(sm, user_id, "mail") == {b}
        assert await _extras(sm, user_id, "sms") == set()


async def test_users_crud_nonexistent_extra_team_is_422_with_field() -> None:
    """Несуществующий id в добавке → 422 unprocessable с именем поля в `details[]` (§5.2)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            await s.commit()
            role_id = role.id

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.post(
                "/api/users",
                json={
                    "username": "Пётр",
                    "role_id": str(role_id),
                    "mail_extra_team_ids": [str(uuid.uuid4())],
                },
            )

    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] == "unprocessable"
    assert error["details"][0]["field"] == "mail_extra_team_ids"


async def test_users_crud_patch_removing_base_team_drops_it_from_both_channels() -> None:
    """`PATCH` с `team_ids` БЕЗ A: команда A исчезает из scope ОБОИХ каналов (§2.3).

    Копии в добавке не остаётся — это и есть «инвариант вычитания»: снятие команды в
    основном блоке безусловно снимает доступ к обоим каналам, без backfill'а.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_a = await seed_team(s, name="Альфа")
            team_b = await seed_team(s, name="Бета")
            await add_membership(s, user.id, team_a.id)
            # Добавки обоих каналов уже содержат B (A как базовая в добавке не хранится).
            await add_extra_team(s, user.id, "mail", team_b.id)
            await add_extra_team(s, user.id, "sms", team_b.id)
            await seed_account(s, account_id=1, team_id=team_a.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id, a, b = user.id, team_a.id, team_b.id

        admin_app = build_app(sm, _admin())
        async with client(admin_app) as c:
            resp = await c.patch(f"/api/users/{user_id}", json={"team_ids": []})
        assert resp.status_code == 200, resp.text

        # A нигде не «осела»: ни в базовых, ни в добавках обоих каналов.
        assert resp.json()["teams"] == []
        assert await _extras(sm, user_id, "mail") == {b}
        assert await _extras(sm, user_id, "sms") == {b}
        assert a not in await _extras(sm, user_id, "mail")

        # Фактический доступ к почте команды A утрачен.
        op_app = build_app(sm, _operator(user_id), mail_client=FakeMailClient())
        async with client(op_app) as c:
            feed = await c.get("/api/mail/messages")
            mutation = await c.patch("/api/mail/mailboxes/1", json={"is_active": False})

    assert feed.json()["messages"] == []
    assert mutation.status_code == 403


async def test_users_crud_patch_adding_team_to_base_removes_its_extra_copy() -> None:
    """`PATCH` с `team_ids`, включающим B (бывшую добавку): дубль в добавке снимается (§2.3).

    Базовое членство и так входит в scope обоих каналов ⇒ хранить его же добавкой — дубль,
    который потом дал бы «висящий» доступ при исключении из команды.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_b = await seed_team(s, name="Бета")
            await add_extra_team(s, user.id, "mail", team_b.id)
            await add_extra_team(s, user.id, "sms", team_b.id)
            await s.commit()
            user_id, b = user.id, team_b.id

        assert await _extras(sm, user_id, "mail") == {b}

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.patch(f"/api/users/{user_id}", json={"team_ids": [str(b)]})
        assert resp.status_code == 200, resp.text

        # B стала базовой ⇒ из добавок ОБОИХ каналов снята.
        assert resp.json()["mail_extra_teams"] == []
        assert await _extras(sm, user_id, "mail") == set()
        assert await _extras(sm, user_id, "sms") == set()


async def test_users_crud_patch_without_extra_field_does_not_change_extras() -> None:
    """`PATCH` без поля добавки → набор канала НЕ меняется (presence-семантика §5.2)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await s.commit()
            user_id, b = user.id, team_b.id

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.patch(f"/api/users/{user_id}", json={"telegram": "@nikita"})
        assert resp.status_code == 200, resp.text

        assert await _extras(sm, user_id, "mail") == {b}


async def test_users_crud_patch_empty_extra_list_clears_channel_addition() -> None:
    """`PATCH` с `mail_extra_team_ids: []` → добавка канала снимается целиком (§5.2)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await add_extra_team(s, user.id, "sms", team_b.id)
            await s.commit()
            user_id, b = user.id, team_b.id

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.patch(f"/api/users/{user_id}", json={"mail_extra_team_ids": []})
        assert resp.status_code == 200, resp.text

        assert await _extras(sm, user_id, "mail") == set()
        # Другой канал не тронут — наборы независимы (§1).
        assert await _extras(sm, user_id, "sms") == {b}


# --- Путь 2: teams CRUD — SECURITY-кейс (§2.3, обязателен отдельно) ------------


async def test_teams_crud_patch_membership_normalizes_mail_extra_no_hanging_access() -> None:
    """SECURITY (§2.3 путь 2, канал `mail`): доп-команда → участник → исключён ⇒ доступа НЕТ.

    Последовательность из разбора §2.3, дословно:
    (а) `X` получает доп-команду `B` (блок «Почты») → строка `(X, mail, B)`;
    (б) `PATCH /api/teams/{B}` с `member_ids`, включающим `X` — строка добавки ОБЯЗАНА
        исчезнуть в ТОЙ ЖЕ транзакции;
    (в) `PATCH /api/teams/{B}` с `member_ids` БЕЗ `X` ⇒ у `X` НЕТ доступа к почте `B`
        (лента пуста, мутация ящика `B` → 403).

    Без нормализации на пути teams CRUD шаг (в) оставил бы «висящий» доступ.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_b = await seed_team(s, name="Бета")
            # (а) доп-команда B в блоке «Почты».
            await add_extra_team(s, user.id, "mail", team_b.id)
            await seed_account(s, account_id=1, team_id=team_b.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 6, 1))
            await s.commit()
            user_id, b = user.id, team_b.id

        assert await _extras(sm, user_id, "mail") == {b}

        admin_app = build_app(sm, _admin())
        async with client(admin_app) as c:
            # (б) X становится УЧАСТНИКОМ B на странице «Команды» (сервис users не звали).
            joined = await c.patch(f"/api/teams/{b}", json={"member_ids": [str(user_id)]})
            assert joined.status_code == 200, joined.text
            # Инвариант §2.3: добавка снята В ТОЙ ЖЕ транзакции.
            assert await _extras(sm, user_id, "mail") == set()

            # (в) X исключён из B.
            left = await c.patch(f"/api/teams/{b}", json={"member_ids": []})
            assert left.status_code == 200, left.text

        assert await _extras(sm, user_id, "mail") == set()

        # Доступа к почте команды B НЕТ — ни на чтение, ни на мутацию.
        op_app = build_app(sm, _operator(user_id), mail_client=FakeMailClient())
        async with client(op_app) as c:
            feed = await c.get("/api/mail/messages")
            catalog = await c.get("/api/mail/mailboxes")
            mutation = await c.patch("/api/mail/mailboxes/1", json={"is_active": False})

    assert feed.json()["messages"] == []
    assert catalog.json()["mailboxes"] == []
    assert mutation.status_code == 403
    assert mutation.json()["error"]["code"] == "forbidden"


async def test_teams_crud_patch_membership_normalizes_sms_extra_no_hanging_access() -> None:
    """SECURITY (§2.3 путь 2, канал `sms`): та же последовательность для СМС.

    Канал `sms` проверяется ОТДЕЛЬНО от `mail` (нормативно: «работает почта» за СМС не
    засчитывается) — `remove_team_for_users` обязан снимать добавку в ОБОИХ каналах.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_b = await seed_team(s, name="Бета")
            await add_extra_team(s, user.id, "sms", team_b.id)
            await s.commit()
            user_id, b = user.id, team_b.id

        assert await _extras(sm, user_id, "sms") == {b}

        admin_app = build_app(sm, _admin())
        async with client(admin_app) as c:
            joined = await c.patch(f"/api/teams/{b}", json={"member_ids": [str(user_id)]})
            assert joined.status_code == 200, joined.text
            assert await _extras(sm, user_id, "sms") == set()

            left = await c.patch(f"/api/teams/{b}", json={"member_ids": []})
            assert left.status_code == 200, left.text

        # После исключения «висящей» добавки канала `sms` не осталось ⇒ scope СМС не
        # содержит B (доступ к номерам/сообщениям команды B утрачен вместе с членством).
        assert await _extras(sm, user_id, "sms") == set()


async def test_teams_crud_patch_membership_normalizes_only_that_team_and_keeps_others() -> None:
    """Нормализация точечная: снимается добавка ТОЛЬКО этой команды, прочие сохраняются."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_b = await seed_team(s, name="Бета")
            team_c = await seed_team(s, name="Гамма")
            await add_extra_team(s, user.id, "mail", team_b.id)
            await add_extra_team(s, user.id, "mail", team_c.id)
            await s.commit()
            user_id, b, c_id = user.id, team_b.id, team_c.id

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.patch(f"/api/teams/{b}", json={"member_ids": [str(user_id)]})
            assert resp.status_code == 200, resp.text

        # B снята (стала базовой), C — осталась добавкой.
        assert await _extras(sm, user_id, "mail") == {c_id}


async def test_teams_crud_create_with_member_keeps_other_extras_intact() -> None:
    """`POST /api/teams` тоже нормализует состав (§2.3 путь 2) — прочие добавки целы.

    ⚠️ У СОЗДАВАЕМОЙ команды id новый ⇒ ничьей добавкой она быть не может: вызов
    `remove_team_for_users` на пути `create` — оборонительный (закрывает путь записи
    целиком, чтобы инвариант не зависел от того, каким эндпоинтом заведён состав).
    Проверяемое здесь: нормализация на `create` НЕ трогает добавки других команд/каналов
    и не создаёт строк для новой команды.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_c = await seed_team(s, name="Гамма")
            await add_extra_team(s, user.id, "mail", team_c.id)
            await add_extra_team(s, user.id, "sms", team_c.id)
            await s.commit()
            user_id, c_id = user.id, team_c.id

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.post(
                "/api/teams",
                json={"name": "Новая", "member_ids": [str(user_id)]},
            )
        assert resp.status_code == 201, resp.text
        new_team_id = uuid.UUID(resp.json()["id"])

        # Новая команда — базовая, добавкой не становится; прочие добавки не тронуты.
        mail_extras = await _extras(sm, user_id, "mail")
        assert mail_extras == {c_id}
        assert new_team_id not in mail_extras
        assert await _extras(sm, user_id, "sms") == {c_id}


async def test_delete_team_cascades_extras_via_api() -> None:
    """`DELETE /api/teams/{id}` снимает добавки этой команды (каскад БД, §2.1)."""
    async with mail_db() as sm:
        async with sm() as s:
            role = await _seed_role_for_users(s)
            user = await seed_user(s, role)
            team_b = await seed_team(s)
            team_c = await seed_team(s)
            await add_extra_team(s, user.id, "mail", team_b.id)
            await add_extra_team(s, user.id, "mail", team_c.id)
            await s.commit()
            user_id, b, c_id = user.id, team_b.id, team_c.id

        app = build_app(sm, _admin())
        async with client(app) as c:
            resp = await c.delete(f"/api/teams/{b}")
        assert resp.status_code == 204, resp.text

        assert await _extras(sm, user_id, "mail") == {c_id}
