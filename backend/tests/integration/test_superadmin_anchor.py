"""Integration (ADR-051): системная строка-якорь супер-админа — граница безопасности.

FastAPI-app + **реальный Postgres** (обязателен: проверяются частичный уникальный индекс
`uq_users_system_singleton`, FK `ON DELETE RESTRICT` роли и `ON CONFLICT DO NOTHING`
bootstrap'а — на моках ни одно из этих поведений не воспроизводится).

Якорь — **идентичность для личного состояния, и ТОЛЬКО она** (ADR-051 §1). Он НЕ учётка,
НЕ источник прав, НЕ способ входа, НЕ канал доставки и НЕ участник команд. Покрыто
(ADR-051 «Последствия · QA (обязательный минимум)»):

- **невидимость в реестре** (§1.4(г)): якоря нет в `GET /api/users`; `PATCH`/`DELETE
  /api/users/{SUPERADMIN_USER_ID}` → `404 user_not_found`;
- **команды** (§1.4(г)): якорь как `leader_id` / `member_ids` → `422` (не существует);
- **вход** (§1.6): логин под зарезервированным `superadmin@system` — с паролем и без —
  → `401`; setup-token НЕ выдаётся (иначе ADR-025-ветка «открытого первого входа» стала бы
  эскалацией до роли `admin`);
- **роли** (§1.5): `DELETE` роли `admin` → `409 role_in_use` (её держит якорь), а НЕ `500`
  IntegrityError; `user_count` роли якоря НЕ включает;
- **bootstrap** (§1.3/§1.1): повторные вызовы — но-оп (идемпотентность, строка не
  перезаписывается); вторая системная строка → `IntegrityError` (singleton-индекс);
- **fan-out доставки** (§1.4(в)): якорь не попадает в получателей Telegram даже при
  искусственно вставленной привязке (защита ЯВНАЯ — `NOT is_system`, а не только INNER JOIN).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.domain.superadmin import SUPERADMIN_USER_ID, SUPERADMIN_USERNAME
from mail_s34_helpers import (
    FakeMailClient,
    add_membership,
    bootstrap_superadmin_anchor,
    build_app,
    build_principal,
    client,
    mail_db,
    seed_role,
    seed_team,
    seed_user,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_ANCHOR_ID = str(SUPERADMIN_USER_ID)


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


def _app(sm: Any, principal: Any | None = None) -> Any:
    """Приложение под супер-админом (проходит `require_admin` и любой `require(...)`)."""
    actor = principal or build_principal(is_superadmin=True)
    return build_app(sm, actor, mail_client=FakeMailClient())


async def _anchor_row(sm: async_sessionmaker[AsyncSession]) -> Any:
    """Строка-якорь из БД напрямую (через API она невидима — в этом и суть §1.4)."""
    async with sm() as s:
        row = await s.execute(
            text(
                "SELECT id, username, password_hash, role_id, is_active, is_system, telegram, "
                "first_login_at FROM users WHERE is_system"
            )
        )
        return row.all()


# --- §1.4(г): невидимость в реестре пользователей ---------------------------


async def test_anchor_is_absent_from_users_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Якоря НЕТ в `GET /api/users` — при том что физически строка в `users` есть."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            await seed_user(s, role, username="alice")
            await s.commit()

        async with client(_app(sm)) as c:
            resp = await c.get("/api/users")

        # Контроль: строка-якорь в БД РЕАЛЬНО существует (иначе тест был бы тавтологией).
        assert len(await _anchor_row(sm)) == 1

    assert resp.status_code == 200
    users = resp.json()["items"]
    ids = [u["id"] for u in users]
    usernames = [u["username"] for u in users]
    assert _ANCHOR_ID not in ids
    assert SUPERADMIN_USERNAME not in usernames
    assert usernames == ["alice"]


async def test_patch_and_delete_anchor_by_id_are_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """`PATCH`/`DELETE /api/users/{SUPERADMIN_USER_ID}` → `404 user_not_found` (§1.4(г)).

    Супер-админ «из UI не редактируется и не удаляется» (ADR-021, US-16): резолв идёт через
    `get_by_id`, а он якорь не возвращает (`NOT is_system`).
    """
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with client(_app(sm)) as c:
            patch = await c.patch(f"/api/users/{_ANCHOR_ID}", json={"is_active": False})
            delete = await c.delete(f"/api/users/{_ANCHOR_ID}")

        # Якорь на месте: 404 — это отказ резолва, а не следствие удаления.
        rows = await _anchor_row(sm)

    assert patch.status_code == 404
    assert patch.json()["error"]["code"] == "user_not_found"
    assert delete.status_code == 404
    assert delete.json()["error"]["code"] == "user_not_found"
    assert len(rows) == 1
    assert rows[0][4] is True  # is_active не сброшен PATCH-ом


# --- §1.4(г): якорь невозможно поставить в команду --------------------------


async def test_anchor_cannot_be_team_leader_or_member_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Якорь как `leader_id` / `member_ids` → `422` (валидация через `get_existing_ids`)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with client(_app(sm)) as c:
            as_leader = await c.post(
                "/api/teams", json={"name": "T-leader", "leader_id": _ANCHOR_ID}
            )
            as_member = await c.post(
                "/api/teams", json={"name": "T-member", "member_ids": [_ANCHOR_ID]}
            )

        # Инвариант пустоты связей (§1.4(в)): у якоря нет строк в `user_teams`.
        async with sm() as s:
            links = await s.execute(
                text("SELECT count(*) FROM user_teams WHERE user_id = :uid"), {"uid": _ANCHOR_ID}
            )
            membership_count = links.scalar_one()

    assert as_leader.status_code == 422
    assert as_member.status_code == 422
    assert membership_count == 0


async def test_anchor_cannot_be_added_to_team_via_patch_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Якорь нельзя дописать в состав существующей команды (`PATCH /api/teams/{id}`) → `422`."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s)
            user = await seed_user(s, role)
            await add_membership(s, user.id, team.id)
            await s.commit()
            team_id, user_id = team.id, user.id

        async with client(_app(sm)) as c:
            resp = await c.patch(
                f"/api/teams/{team_id}",
                json={"member_ids": [str(user_id), _ANCHOR_ID]},
            )

    assert resp.status_code == 422


# --- §1.6: вход под зарезервированным username невозможен -------------------


async def test_login_as_reserved_anchor_username_is_401_without_setup_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Логин под `superadmin@system` — с паролем и БЕЗ пароля — → `401`; setup-token НЕ выдаётся.

    Ключевой security-кейс (ADR-051 §1.1, альтернатива 4): будь у якоря `password_hash IS
    NULL`, беспарольная ветка «открытого первого входа» (ADR-025) отдала бы setup-token
    любому, кто назовёт этот `username` → эскалация до роли `admin`. Две независимые
    преграды: locked bcrypt-хэш случайного секрета И невидимость в `get_by_username`.
    """
    await _enable_mail(monkeypatch)
    async with mail_db() as sm, client(_app(sm)) as c:
        with_password = await c.post(
            "/api/auth/login",
            json={"username": SUPERADMIN_USERNAME, "password": "any-password"},
        )
        without_password = await c.post("/api/auth/login", json={"username": SUPERADMIN_USERNAME})

    for resp in (with_password, without_password):
        assert resp.status_code == 401
        body = resp.json()
        # Ни setup-token, ни access-token: ни одна ветка входа к якорю неприменима.
        assert "setup_token" not in body
        assert "access_token" not in body
        assert body.get("password_setup_required") is None


# --- §1.5: роли — счётчик не врёт, FK не ломается ---------------------------


async def test_delete_role_held_by_anchor_is_409_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """`DELETE` роли `admin` (её держит якорь) → `409 role_in_use`, а НЕ `500` IntegrityError.

    `is_in_use` — зеркало FK `users.role_id → roles.id ON DELETE RESTRICT` и обязан ВИДЕТЬ
    якорь (§1.5). Осознанное следствие: роль якоря неудаляема даже при `user_count = 0`.
    """
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with client(_app(sm)) as c:
            roles = (await c.get("/api/roles")).json()
            admin_role = next(r for r in roles["items"] if r["name"] == "admin")
            resp = await c.delete(f"/api/roles/{admin_role['id']}")

        # Роль на месте — 409 не «съел» её частично.
        async with sm() as s:
            still = await s.execute(text("SELECT count(*) FROM roles WHERE name = 'admin'"))
            assert still.scalar_one() == 1

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "role_in_use"
    # Счётчик носителей роли якорь НЕ включает (§1.5: отображение исключает `is_system`),
    # но удалить её всё равно нельзя — в этом и состоит нормативная асимметрия.
    assert admin_role["user_count"] == 0


async def test_role_user_count_excludes_anchor(monkeypatch: pytest.MonkeyPatch) -> None:
    """`user_count` роли якоря не получает фантомного «+1» (§1.5, `list_all_with_counts`)."""
    await _enable_mail(monkeypatch)
    async with mail_db() as sm:
        async with sm() as s:
            # Кладём РЕАЛЬНОГО пользователя в ту же роль `admin`, что держит якорь.
            admin_role = (
                await s.execute(text("SELECT id FROM roles WHERE name = 'admin'"))
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, role_id, is_active, "
                    "is_system) VALUES (:id, 'real-admin', 'x', :rid, true, false)"
                ),
                {"id": str(uuid.uuid4()), "rid": str(admin_role)},
            )
            await s.commit()

        async with client(_app(sm)) as c:
            roles = (await c.get("/api/roles")).json()

        # В БД роль `admin` держат ДВОЕ (якорь + real-admin) — счётчик обязан показать 1.
        async with sm() as s:
            physical = await s.execute(
                text("SELECT count(*) FROM users WHERE role_id = :rid"), {"rid": str(admin_role)}
            )
            assert physical.scalar_one() == 2

    admin = next(r for r in roles["items"] if r["name"] == "admin")
    assert admin["user_count"] == 1


# --- §1.3/§1.1: bootstrap идемпотентен; singleton-индекс держит ------------


async def test_bootstrap_is_idempotent_and_does_not_rewrite_existing_row() -> None:
    """Повторные вызовы `ensure_superadmin_anchor` — но-оп: строка одна и НЕ перезаписана.

    Критично, что не перезаписана: перезапись ротировала бы пароль-заглушку и (при ошибке
    реализации через UPSERT) могла бы затронуть личное состояние. Проверяем стабильность
    `password_hash` — он генерируется случайным при КАЖДОМ вызове, поэтому его неизменность
    доказывает, что второй вызов действительно ничего не записал.
    """
    async with mail_db() as sm:  # фикстура уже сделала первый bootstrap
        before = await _anchor_row(sm)

        # Повторный старт приложения / второй воркер / рестарт.
        await bootstrap_superadmin_anchor(sm)
        await bootstrap_superadmin_anchor(sm)

        after = await _anchor_row(sm)

        async with sm() as s:
            admin_roles = await s.execute(text("SELECT count(*) FROM roles WHERE name = 'admin'"))
            role_count = admin_roles.scalar_one()

    assert len(before) == 1
    assert len(after) == 1
    assert before[0] == after[0]  # id, username, password_hash, role_id, ... — всё то же
    # Роль-заглушка тоже создаётся ровно один раз (шаг (3) цепочки не дублирует).
    assert role_count == 1


async def test_anchor_row_matches_normative_values() -> None:
    """Нормативные значения строки-якоря (ADR-051 §1.1): id/username/locked-хэш/флаги."""
    async with mail_db() as sm:
        rows = await _anchor_row(sm)

    assert len(rows) == 1
    anchor_id, username, password_hash, _role_id, is_active, is_system, telegram, first_login = (
        rows[0]
    )
    assert anchor_id == SUPERADMIN_USER_ID
    assert username == SUPERADMIN_USERNAME
    # «Locked account»: NULL ЗАПРЕЩЁН (иначе беспарольная ветка ADR-025 = дыра эскалации).
    assert password_hash is not None
    assert password_hash.startswith("$2")  # bcrypt
    assert is_active is True
    assert is_system is True
    # Якорь не резолвится Telegram-SSO и не входит через БД-ветку.
    assert telegram is None
    assert first_login is None


async def test_second_system_row_violates_singleton_index() -> None:
    """Вторая системная строка → `IntegrityError` (`uq_users_system_singleton`, §1.1).

    Индекс объявлен в МОДЕЛИ (миграция его лишь зеркалит) — иначе схема тестов
    (`metadata.create_all`) разошлась бы с продом и регрессия, создающая второй якорь,
    прошла бы зелёные тесты и упала только на проде.
    """
    async with mail_db() as sm:
        async with sm() as s:
            role_id = (
                await s.execute(text("SELECT id FROM roles WHERE name = 'admin'"))
            ).scalar_one()

            with pytest.raises(IntegrityError):
                await s.execute(
                    text(
                        "INSERT INTO users (id, username, password_hash, role_id, is_active, "
                        "is_system) VALUES (:id, 'second@system', 'x', :rid, true, true)"
                    ),
                    {"id": str(uuid.uuid4()), "rid": str(role_id)},
                )
                await s.commit()
            await s.rollback()

        # Якорь по-прежнему ровно один.
        assert len(await _anchor_row(sm)) == 1


# --- §1.4(в): fan-out доставки якорь НЕ включает ----------------------------


async def test_anchor_is_excluded_from_telegram_fanout_even_with_forced_link() -> None:
    """Якоря нет в получателях Telegram — даже если привязку вставить в обход API (§1.4(в)).

    Через API привязка невозможна (`403`, §1.6), поэтому строку `mail_telegram_links` для
    якоря вставляем **прямым SQL**: именно так проверяется, что защита ЯВНАЯ (`NOT
    is_system`), а не держится лишь на INNER JOIN. Без явного фильтра `sees_all_candidates`
    впустил бы якорь в admin-получатели ВСЕХ писем — роль якоря `admin` (полный каталог).
    """
    from app.repositories.mail_telegram_link_repository import MailTelegramLinkRepository

    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role, username="operator")
            await add_membership(s, user.id, team.id)
            await s.commit()
            team_id, user_id = team.id, user.id

        async with sm() as s:
            # Привязка живого оператора (легальная) — контрольная группа.
            await s.execute(
                text(
                    "INSERT INTO mail_telegram_links (user_id, telegram_user_id, username)"
                    " VALUES (:uid, 111, 'operator')"
                ),
                {"uid": str(user_id)},
            )
            # Привязка ЯКОРЯ — искусственная, в обход 403. Плюс членство в команде прямым SQL
            # (через API — 422), чтобы снять и второй неявный INNER JOIN (`user_teams`).
            await s.execute(
                text(
                    "INSERT INTO mail_telegram_links (user_id, telegram_user_id, username)"
                    " VALUES (:uid, 999, 'anchor')"
                ),
                {"uid": _ANCHOR_ID},
            )
            await s.execute(
                text("INSERT INTO user_teams (user_id, team_id) VALUES (:uid, :tid)"),
                {"uid": _ANCHOR_ID, "tid": str(team_id)},
            )
            await s.commit()

        async with sm() as s:
            repo = MailTelegramLinkRepository(s)
            recipients = await repo.team_recipients(team_id)
            candidates = await repo.sees_all_candidates()

    # Оба INNER JOIN'а искусственно «удовлетворены» — якорь отсекает ТОЛЬКО явный фильтр.
    recipient_ids = [r.user_id for r in recipients]
    assert SUPERADMIN_USER_ID not in recipient_ids
    assert recipient_ids == [user_id]

    candidate_ids = [c.user_id for c in candidates]
    assert SUPERADMIN_USER_ID not in candidate_ids
    assert candidate_ids == [user_id]


async def test_anchor_is_excluded_from_sms_fanout_even_with_forced_link() -> None:
    """То же для SMS-получателей (`SmsTelegramLinkRepository.recipients_for_team`, §1.4(в))."""
    from app.repositories.sms_telegram_link_repository import SmsTelegramLinkRepository

    async with mail_db() as sm:
        async with sm() as s:
            team = await seed_team(s)
            role = await seed_role(s, permissions={"sms": ["view"]})
            user = await seed_user(s, role, username="sms-operator")
            await add_membership(s, user.id, team.id)
            await s.commit()
            team_id, user_id = team.id, user.id

        async with sm() as s:
            await s.execute(
                text(
                    "INSERT INTO sms_telegram_links (user_id, telegram_user_id)"
                    " VALUES (:uid, 222)"
                ),
                {"uid": str(user_id)},
            )
            await s.execute(
                text(
                    "INSERT INTO sms_telegram_links (user_id, telegram_user_id)"
                    " VALUES (:uid, 888)"
                ),
                {"uid": _ANCHOR_ID},
            )
            await s.execute(
                text("INSERT INTO user_teams (user_id, team_id) VALUES (:uid, :tid)"),
                {"uid": _ANCHOR_ID, "tid": str(team_id)},
            )
            await s.commit()

        async with sm() as s:
            recipients = await SmsTelegramLinkRepository(s).recipients_for_team(team_id)

    recipient_ids = [r.user_id for r in recipients]
    assert SUPERADMIN_USER_ID not in recipient_ids
    assert recipient_ids == [user_id]


# --- §1.4(а): якорь невидим для резолва субъекта ----------------------------


async def test_anchor_is_invisible_to_user_repository_resolvers() -> None:
    """`UserRepository`-резолверы субъекта якоря НЕ возвращают, но uniqueness-чек его ВИДИТ.

    Асимметрия нормативна (§1.4(а)): методы-резолверы фильтруют `NOT is_system`, а
    `exists_by_username` — зеркало DB-констрейнта и обязан видеть все строки (иначе `409`
    подменился бы `500`-IntegrityError).
    """
    from app.models.user import User
    from app.repositories.user_repository import UserRepository

    async with mail_db() as sm, sm() as s:
        repo = UserRepository(s)

        by_id = await repo.get_by_id(SUPERADMIN_USER_ID)
        by_username = await repo.get_by_username(SUPERADMIN_USERNAME)
        listed = await repo.list_all()
        existing = await repo.get_existing_ids({SUPERADMIN_USER_ID})
        with_teams = await repo.get_with_teams(SUPERADMIN_USER_ID)
        deleted = await repo.delete_by_id(SUPERADMIN_USER_ID)

        # Uniqueness-чек ВИДИТ якорь (иначе INSERT с этим username дал бы 500, а не 409).
        taken = await repo.exists_by_username(SUPERADMIN_USERNAME)

        # Строка физически на месте — `delete_by_id` вернул False, а не удалил её.
        physical = (
            await s.execute(select(User).where(User.id == SUPERADMIN_USER_ID))
        ).scalar_one_or_none()

    assert by_id is None
    assert by_username is None
    assert [u.username for u in listed] == []
    assert existing == set()
    assert with_teams is None
    assert deleted is False
    assert taken is True
    assert physical is not None
    assert physical.is_system is True
