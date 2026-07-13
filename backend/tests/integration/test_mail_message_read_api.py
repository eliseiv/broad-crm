"""Integration (ADR-050 §2): ЛИЧНАЯ прочитанность писем — POST/DELETE `…/read`, `is_unread`.

FastAPI-app + **реальный Postgres** (обязателен: детект гонки читает имя нарушенной
constraint из `exc.orig.__cause__` asyncpg — на моках/SQLite регрессия не ловится).

Покрыто (ADR-050 §2.2/§2.3/§2.8 + **ADR-051 §2**, «Последствия · QA»):
- идемпотентность POST/DELETE (повтор → 204; `read_at` при повторе НЕ обновляется);
- 404 mail_message_not_found на чужое письмо (вне `MailScope`) и на несуществующее —
  неотличимы (анти-энумерация);
- **супер-админ из `.env` имеет ПОЛНОЦЕННОЕ личное состояние** (ADR-051 §2 отменяет норму
  ADR-050 §2.5): отмечает письмо → `204`, откатывает → `204`, `is_unread` — реальное личное
  значение, `unread=true` отдаёт непрочитанные (а не пустую страницу). Его идентичность —
  системная строка-якорь `SUPERADMIN_USER_ID` (ADR-051 §1.1), и отметки персональны:
  прочтение супер-админом не гасит индикатор БД-пользователю;
- персональность: два пользователя, одно письмо — разные `is_unread`;
- фильтр `unread=true`: курсорная догрузка (страница 2) без потерь/дублей; AND с фильтрами
  ящика/команды;
- CASCADE: удаление ящика / письма / пользователя чистит отметки;
- ГОНКА: письмо удалено между scope-проверкой и `INSERT` → **404, НЕ 500**; нарушение FK по
  `user_id` под 404 **НЕ маскируется** (500).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_VIEW_ONLY = {"mail": ["view"]}


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


def _app(sm: Any, principal: Any) -> Any:
    return build_app(sm, principal, mail_client=FakeMailClient())


def _operator(user_id: uuid.UUID) -> Any:
    """Не-admin оператор почты (`mail:view`, scope сужен командами пользователя)."""
    return build_principal(user_id=user_id, is_superadmin=False, permissions=_VIEW_ONLY)


def _db_admin(user_id: uuid.UUID) -> Any:
    """БД-пользователь с полным каталогом прав ⇒ `sees_all_mail_teams`, но `user_id` есть."""
    return build_principal(user_id=user_id, is_superadmin=False)


def _env_superadmin() -> Any:
    """Супер-админ из `.env` (ADR-008): идентичность — константа якоря (ADR-051 §1.2).

    `user_id` НЕ передаём намеренно — билдер подставляет `SUPERADMIN_USER_ID` ровно так же,
    как это делает прод (`get_current_principal`, без запроса в БД). Сама строка-якорь есть
    в тестовой БД: её сеет фикстура `mail_db()` (ADR-051 §1.3).
    """
    return build_principal(is_superadmin=True)


async def _read_rows(sm: async_sessionmaker[AsyncSession]) -> list[tuple[Any, ...]]:
    async with sm() as s:
        rows = await s.execute(text("SELECT user_id, message_id, read_at FROM mail_message_reads"))
        return [tuple(r) for r in rows.all()]


# --- Идемпотентность --------------------------------------------------------


async def test_mark_read_twice_returns_204_and_does_not_touch_read_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повторный POST — тоже 204; `read_at` НЕ обновляется (важно ПЕРВОЕ открытие, §2.2)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _operator(user_id))) as c:
            first = await c.post("/api/mail/messages/1/read")
            rows_after_first = await _read_rows(sm)
            second = await c.post("/api/mail/messages/1/read")
            rows_after_second = await _read_rows(sm)

    assert first.status_code == 204
    assert second.status_code == 204
    # Ровно одна отметка, `read_at` не переписан повторным вызовом.
    assert len(rows_after_first) == 1
    assert len(rows_after_second) == 1
    assert rows_after_first[0][2] == rows_after_second[0][2]


async def test_unmark_read_is_idempotent_without_existing_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DELETE без предшествующей отметки → 204 (строки не было — не ошибка, §2.2)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _operator(user_id))) as c:
            first = await c.delete("/api/mail/messages/1/read")
            await c.post("/api/mail/messages/1/read")
            second = await c.delete("/api/mail/messages/1/read")
            third = await c.delete("/api/mail/messages/1/read")

    assert [first.status_code, second.status_code, third.status_code] == [204, 204, 204]
    assert await _read_rows(sm) == []


# --- 404: чужое письмо неотличимо от несуществующего (анти-энумерация) -------


async def test_foreign_and_missing_message_are_indistinguishable_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Письмо вне `MailScope` и несуществующее дают ОДИН и тот же 404 (§2.3)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            my_team = await seed_team(s, name="mine")
            other_team = await seed_team(s, name="other")
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, my_team.id)
            await seed_account(s, account_id=1, team_id=my_team.id)
            await seed_account(s, account_id=2, team_id=other_team.id)
            # id=1 — моё письмо; id=2 — чужое (ящик чужой команды).
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await seed_message(s, account_id=2, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _operator(user_id))) as c:
            foreign_post = await c.post("/api/mail/messages/2/read")
            missing_post = await c.post("/api/mail/messages/999999/read")
            foreign_delete = await c.delete("/api/mail/messages/2/read")
            missing_delete = await c.delete("/api/mail/messages/999999/read")
            mine = await c.post("/api/mail/messages/1/read")

    assert mine.status_code == 204
    for resp in (foreign_post, missing_post, foreign_delete, missing_delete):
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "mail_message_not_found"
    # Тела ответов совпадают побайтно — по ответу нельзя узнать о существовании письма.
    assert foreign_post.json() == missing_post.json()
    assert foreign_delete.json() == missing_delete.json()
    # Отметка на чужое письмо не создана.
    assert [r[1] for r in await _read_rows(sm)] == [1]


# --- Супер-админ из `.env`: ПОЛНОЦЕННОЕ личное состояние (ADR-051 §2) --------
#
# Норма ADR-050 §2.5 («403 на отметку, is_unread всегда false, unread=true → пустая
# страница») ОТМЕНЕНА ADR-051 §2. Идентичность супер-админа — системная строка-якорь
# (SUPERADMIN_USER_ID, ADR-051 §1.1), поэтому FK `mail_message_reads.user_id → users.id`
# выполним и прочитанность работает ровно как у БД-пользователя.


async def test_env_superadmin_marks_and_unmarks_read_204(monkeypatch: pytest.MonkeyPatch) -> None:
    """Супер-админ отмечает письмо → `204`; отметка пишется на `SUPERADMIN_USER_ID`.

    Разворот ADR-050 §2.5 (было `403`). Откат в «непрочитано» — тоже `204`, строка снимается.
    """
    from app.domain.superadmin import SUPERADMIN_USER_ID

    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()

        async with client(_app(sm, _env_superadmin())) as c:
            post = await c.post("/api/mail/messages/1/read")
            rows_after_mark = await _read_rows(sm)
            repeat = await c.post("/api/mail/messages/1/read")  # идемпотентность
            delete = await c.delete("/api/mail/messages/1/read")
            rows_after_unmark = await _read_rows(sm)

    assert post.status_code == 204
    assert repeat.status_code == 204
    assert delete.status_code == 204
    # Отметка принадлежит ЯКОРЮ (константный user_id), а не «никому».
    assert [(r[0], r[1]) for r in rows_after_mark] == [(SUPERADMIN_USER_ID, 1)]
    assert rows_after_unmark == []


async def test_env_superadmin_is_unread_is_real_and_personal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`is_unread` супер-админа — РЕАЛЬНОЕ личное значение, а не константа `false` (§2).

    Плюс персональность в обе стороны: прочтение супер-админом не гасит индикатор
    БД-пользователю той же команды (у якоря собственный `user_id`).
    """
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 7, 1))
            await seed_message(s, account_id=1, uid=2, internal_date=dt(2026, 7, 2))
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _env_superadmin())) as c:
            before = await c.get("/api/mail/messages")
            assert (await c.post("/api/mail/messages/2/read")).status_code == 204
            after = await c.get("/api/mail/messages")

        # Тот же ящик глазами БД-пользователя команды — его состояние НЕ затронуто.
        async with client(_app(sm, _operator(user_id))) as c:
            operator_feed = await c.get("/api/mail/messages")

    # До отметки оба письма непрочитаны (а не «всегда false», как было в §2.5).
    assert [m["is_unread"] for m in before.json()["messages"]] == [True, True]
    # Порядок `internal_date DESC` ⇒ первым идёт письмо id=2 (оно и прочитано).
    assert [(m["id"], m["is_unread"]) for m in after.json()["messages"]] == [(2, False), (1, True)]
    # Прочитанность ЛИЧНАЯ: у оператора оба письма по-прежнему непрочитаны.
    assert [m["is_unread"] for m in operator_feed.json()["messages"]] == [True, True]


async def test_env_superadmin_unread_filter_returns_messages_not_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`unread=true` под супер-админом отдаёт непрочитанные — НЕ пустую страницу (§2).

    Прямой разворот ADR-050 §2.5: анти-джойн (ADR-050 §2.4) работает по `user_id` якоря.
    """
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 7, 1))
            await seed_message(s, account_id=1, uid=2, internal_date=dt(2026, 7, 2))
            await s.commit()

        async with client(_app(sm, _env_superadmin())) as c:
            unread_all = await c.get("/api/mail/messages", params={"unread": "true"})
            assert (await c.post("/api/mail/messages/2/read")).status_code == 204
            unread_after = await c.get("/api/mail/messages", params={"unread": "true"})

    # Было бы `[]` по отменённой норме §2.5 — теперь обычная выборка.
    assert [m["id"] for m in unread_all.json()["messages"]] == [2, 1]
    # Прочитанное письмо выпадает из фильтра.
    assert [m["id"] for m in unread_after.json()["messages"]] == [1]


# --- Персональность ---------------------------------------------------------


async def test_is_unread_is_personal_per_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """Одно письмо, два пользователя одной команды — разные значения `is_unread` (§2.2)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            alice = await seed_user(s, role, username="alice")
            bob = await seed_user(s, role, username="bob")
            await add_membership(s, alice.id, team.id)
            await add_membership(s, bob.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            alice_id, bob_id = alice.id, bob.id

        async with client(_app(sm, _operator(alice_id))) as c:
            assert (await c.post("/api/mail/messages/1/read")).status_code == 204
            alice_feed = await c.get("/api/mail/messages")
        async with client(_app(sm, _operator(bob_id))) as c:
            bob_feed = await c.get("/api/mail/messages")
            bob_unread = await c.get("/api/mail/messages", params={"unread": "true"})

    # Прочтение Алисой НЕ гасит индикатор Бобу (прочитанность личная, не командная).
    assert alice_feed.json()["messages"][0]["is_unread"] is False
    assert bob_feed.json()["messages"][0]["is_unread"] is True
    assert [m["id"] for m in bob_unread.json()["messages"]] == [1]


async def test_unmark_returns_message_to_unread_for_that_user_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DELETE …/read` возвращает письмо в «непрочитано» текущему пользователю (§2.7)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _operator(user_id))) as c:
            await c.post("/api/mail/messages/1/read")
            read_feed = await c.get("/api/mail/messages")
            assert (await c.delete("/api/mail/messages/1/read")).status_code == 204
            unread_feed = await c.get("/api/mail/messages")

    assert read_feed.json()["messages"][0]["is_unread"] is False
    assert unread_feed.json()["messages"][0]["is_unread"] is True


# --- Фильтр unread=true: курсор и AND-комбинации ----------------------------


async def test_unread_filter_paginates_by_cursor_without_loss_or_dup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Страница 2 фильтра `unread=true` не теряет и не дублирует письма (курсор жив, §2.4)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            for uid in range(1, 6):
                await seed_message(s, account_id=1, uid=uid, internal_date=dt(2026, 7, uid))
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _operator(user_id))) as c:
            # Прочитано письмо id=4 → выпадает из выборки `unread=true`.
            assert (await c.post("/api/mail/messages/4/read")).status_code == 204

            collected: list[int] = []
            cursor: str | None = None
            for _ in range(5):  # страховка от бесконечного цикла
                params: dict[str, Any] = {"unread": "true", "limit": 2}
                if cursor:
                    params["before"] = cursor
                page = await c.get("/api/mail/messages", params=params)
                assert page.status_code == 200
                body = page.json()
                collected.extend(m["id"] for m in body["messages"])
                cursor = body["next_cursor"]
                if cursor is None:
                    break

    # Порядок `internal_date DESC, id DESC`; прочитанное (4) отсутствует; без дублей/потерь.
    assert collected == [5, 3, 2, 1]
    assert len(collected) == len(set(collected))
    assert cursor is None


async def test_unread_filter_and_combines_with_account_and_team_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`unread` AND-комбинируется с `mail_account_id` и `team_id` (§2.2)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team_a = await seed_team(s, name="A")
            team_b = await seed_team(s, name="B")
            role = await seed_role(s)
            admin_user = await seed_user(s, role, username="admin-db")
            await seed_account(s, account_id=1, team_id=team_a.id)
            await seed_account(s, account_id=2, team_id=team_b.id)
            # id=1,2 — ящик 1 (команда A); id=3 — ящик 2 (команда B).
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 7, 1))
            await seed_message(s, account_id=1, uid=2, internal_date=dt(2026, 7, 2))
            await seed_message(s, account_id=2, uid=1, internal_date=dt(2026, 7, 3))
            await s.commit()
            admin_id, team_a_id, team_b_id = admin_user.id, team_a.id, team_b.id

        async with client(_app(sm, _db_admin(admin_id))) as c:
            # Прочитано id=2 (ящик 1) → в unread-выборке ящика 1 останется только id=1.
            assert (await c.post("/api/mail/messages/2/read")).status_code == 204

            by_account = await c.get(
                "/api/mail/messages", params={"unread": "true", "mail_account_id": 1}
            )
            by_team_a = await c.get(
                "/api/mail/messages", params={"unread": "true", "team_id": str(team_a_id)}
            )
            by_team_b = await c.get(
                "/api/mail/messages", params={"unread": "true", "team_id": str(team_b_id)}
            )
            unread_all = await c.get("/api/mail/messages", params={"unread": "true"})

    assert [m["id"] for m in by_account.json()["messages"]] == [1]
    assert [m["id"] for m in by_team_a.json()["messages"]] == [1]
    assert [m["id"] for m in by_team_b.json()["messages"]] == [3]
    # Без фильтров ящика/команды — все непрочитанные (id=2 прочитано).
    assert [m["id"] for m in unread_all.json()["messages"]] == [3, 1]


# --- CASCADE ----------------------------------------------------------------


async def test_cascade_delete_mailbox_message_and_user_clears_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Удаление письма / ящика / пользователя чистит отметки (`ON DELETE CASCADE`, §2.1)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_account(s, account_id=2, team_id=team.id)
            # id=1 — ящик 1; id=2,3 — ящик 2.
            await seed_message(s, account_id=1, uid=1, internal_date=dt(2026, 7, 1))
            await seed_message(s, account_id=2, uid=1, internal_date=dt(2026, 7, 2))
            await seed_message(s, account_id=2, uid=2, internal_date=dt(2026, 7, 3))
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _operator(user_id))) as c:
            for message_id in (1, 2, 3):
                assert (await c.post(f"/api/mail/messages/{message_id}/read")).status_code == 204
        assert sorted(r[1] for r in await _read_rows(sm)) == [1, 2, 3]

        # 1) Удаление ПИСЬМА чистит его отметку.
        async with sm() as s:
            await s.execute(text("DELETE FROM mail_messages WHERE id = 3"))
            await s.commit()
        assert sorted(r[1] for r in await _read_rows(sm)) == [1, 2]

        # 2) Удаление ЯЩИКА → CASCADE письма → CASCADE отметки.
        async with sm() as s:
            await s.execute(text("DELETE FROM mail_accounts WHERE id = 2"))
            await s.commit()
        assert sorted(r[1] for r in await _read_rows(sm)) == [1]

        # 3) Удаление ПОЛЬЗОВАТЕЛЯ уносит его отметки.
        async with sm() as s:
            await s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": str(user_id)})
            await s.commit()
        assert await _read_rows(sm) == []


# --- Гонка: письмо удалено между scope-проверкой и INSERT --------------------


async def test_message_deleted_between_scope_check_and_insert_maps_to_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ГОНКА (§2.2): FK `message_id` нарушен → 404 mail_message_not_found, а НЕ 500.

    Реальный Postgres обязателен: имя нарушенной constraint приходит через
    `exc.orig.__cause__` (asyncpg `ForeignKeyViolationError.constraint_name`) — на моках
    этот детект не проверяется. Гонку воспроизводим детерминированно: письмо удаляется в
    ОТДЕЛЬНОЙ (закоммиченной) транзакции сразу после scope-проверки, до `INSERT`.
    """
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        from app.services.mail_service import MailService

        original = MailService._load_message_in_scope

        async def racing_load(self: Any, scope: Any, message_id: int) -> Any:
            message = await original(self, scope, message_id)
            # Конкурентная транзакция удаляет письмо ПОСЛЕ проверки видимости.
            async with sm() as other:
                await other.execute(
                    text("DELETE FROM mail_messages WHERE id = :mid"), {"mid": message_id}
                )
                await other.commit()
            return message

        monkeypatch.setattr(MailService, "_load_message_in_scope", racing_load)

        async with client(_app(sm, _operator(user_id))) as c:
            resp = await c.post("/api/mail/messages/1/read")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_message_not_found"
    assert await _read_rows(sm) == []


async def test_user_fk_violation_is_not_masked_as_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нарушение FK по `user_id` под 404 НЕ маскируется (§2.2) — это штатная 500-ситуация."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()

        # Принципал видит все команды (полный каталог), но строки `users` для его `uid` нет
        # ⇒ INSERT нарушит FK `user_id`, а НЕ FK `message_id` (письмо на месте).
        ghost = build_principal(user_id=uuid.uuid4(), is_superadmin=False)
        app = _app(sm, ghost)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/mail/messages/1/read")

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    # Ключевое: это НЕ 404 — «письмо не найдено» такой ситуацией не притворяется.
    assert resp.status_code != 404
    assert await _read_rows(sm) == []


# --- RBAC-гейт --------------------------------------------------------------


async def test_read_endpoints_require_mail_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гейт обеих ручек — `require("mail","view")`; без права → 403 (§2.3)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions={"servers": ["view"]})
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        no_mail = build_principal(
            user_id=user_id, is_superadmin=False, permissions={"servers": ["view"]}
        )
        async with client(_app(sm, no_mail)) as c:
            post = await c.post("/api/mail/messages/1/read")
            delete = await c.delete("/api/mail/messages/1/read")

    assert post.status_code == 403
    assert delete.status_code == 403
    assert await _read_rows(sm) == []


async def test_read_state_row_binds_user_and_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отметка пишется парой `(user_id, message_id)` — PK составной (§2.1)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions=_VIEW_ONLY)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await seed_account(s, account_id=1, team_id=team.id)
            await seed_message(s, account_id=1, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        async with client(_app(sm, _operator(user_id))) as c:
            assert (await c.post("/api/mail/messages/1/read")).status_code == 204

        from app.models.mail_message_read import MailMessageRead

        async with sm() as s:
            rows = (await s.execute(select(MailMessageRead))).scalars().all()

    assert len(rows) == 1
    assert rows[0].user_id == user_id
    assert rows[0].message_id == 1
    assert rows[0].read_at is not None
