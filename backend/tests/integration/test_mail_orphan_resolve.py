"""Integration S4 (ADR-044 §6): ленивый резолв orphan-линков + доставка по chat_id.

Реальный Postgres. Проверяет два триггера связывания orphan-линка (`user_id IS NULL`):
(1) синхронный хук user-сервиса — создание/правка пользователя с `users.telegram`,
совпадающим с orphan-username, связывает линк; (2) reconcile-проход диспетчера — тот же
эффект как safety-net. Регистронезависимость. Плюс: смена username у УЖЕ связанного
линка не ломает доставку (ключ — `chat_id`).
"""

from __future__ import annotations

import uuid

import pytest
from app.repositories.mail_telegram_link_repository import MailTelegramLinkRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_channel_team_repository import UserChannelTeamRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreateRequest, UserUpdateRequest
from app.services.user_service import UserService
from mail_s34_helpers import (
    mail_db,
    seed_link,
    seed_role,
    seed_user,
)
from sqlalchemy import text as sa_text


def _user_service(session: object) -> UserService:
    return UserService(
        users=UserRepository(session),  # type: ignore[arg-type]
        roles=RoleRepository(session),  # type: ignore[arg-type]
        teams=TeamRepository(session),  # type: ignore[arg-type]
        channels=UserChannelTeamRepository(session),  # type: ignore[arg-type]
    )


async def _link_user_id(sm: object, telegram_user_id: int) -> uuid.UUID | None:
    async with sm() as s:  # type: ignore[operator]
        row = (
            await s.execute(
                sa_text("SELECT user_id FROM mail_telegram_links WHERE telegram_user_id=:c"),
                {"c": telegram_user_id},
            )
        ).first()
    return row[0] if row else None


# --- Триггер 1: синхронный хук user-сервиса при СОЗДАНИИ ----------------------
async def test_orphan_bound_on_user_create(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            # orphan-линк ждёт пользователя с username `katetown` (lowercase-норма).
            await seed_link(s, telegram_user_id=101, user_id=None, username="katetown")
            await s.commit()
            role_id = role.id
        async with sm() as s:
            svc = _user_service(s)
            # Создаём пользователя с telegram `@Katetown` — нормализуется в `katetown`.
            await svc.create_user(
                UserCreateRequest(username="katya", telegram="@Katetown", role_id=role_id)
            )
        bound = await _link_user_id(sm, 101)
    assert bound is not None  # orphan связан синхронным хуком при создании


# --- Триггер 1: синхронный хук при ПРАВКЕ (смена users.telegram) --------------
async def test_orphan_bound_on_user_update_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role, username="anna", telegram=None)
            await seed_link(s, telegram_user_id=102, user_id=None, username="anellie_sss")
            await s.commit()
            uid = user.id
        async with sm() as s:
            svc = _user_service(s)
            await svc.update_user(uid, UserUpdateRequest(telegram="Anellie_sss"))
        bound = await _link_user_id(sm, 102)
    assert bound == uid  # смена telegram связала ожидавший orphan


# --- Триггер 2: reconcile-проход диспетчера ----------------------------------
async def test_reconcile_binds_orphan_case_insensitive() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            # Пользователь уже существует (telegram с заглавными), orphan ждёт (lowercase).
            user = await seed_user(s, role, telegram="Loveink")
            await seed_link(s, telegram_user_id=103, user_id=None, username="loveink")
            await s.commit()
            uid = user.id
        async with sm() as s:
            bound_count = await MailTelegramLinkRepository(s).reconcile_orphans()
            await s.commit()
        bound = await _link_user_id(sm, 103)
    # reconcile: lower(users.telegram)=link.username → связывает регистронезависимо.
    assert bound_count == 1
    assert bound == uid


async def test_reconcile_ignores_already_bound() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role, telegram="michtl")
            await seed_link(s, telegram_user_id=104, user_id=user.id, username="michtl")
            await s.commit()
        async with sm() as s:
            bound_count = await MailTelegramLinkRepository(s).reconcile_orphans()
            await s.commit()
    assert bound_count == 0  # уже связанные не трогаются


# --- Доставка по chat_id независима от смены username -------------------------
async def test_delivery_by_chat_id_survives_username_change() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role, telegram="oldname")
            team = await seed_team_local(s)
            await s.execute(
                sa_text("INSERT INTO user_teams (user_id, team_id) VALUES (:u,:t)"),
                {"u": user.id, "t": team},
            )
            # Линк связан по user_id; username в линке УСТАРЕЛ (сменился в Telegram).
            await seed_link(s, telegram_user_id=105, user_id=user.id, username="stale_username")
            await s.commit()
            team_id = team
        async with sm() as s:
            recipients = await MailTelegramLinkRepository(s).team_recipients(team_id)
    # Доставка резолвится по user_id→chat_id; устаревший username в линке не мешает.
    assert any(r.telegram_user_id == 105 for r in recipients)


async def seed_team_local(session: object) -> uuid.UUID:
    """Локальный seed команды, возвращает id (для team_recipients)."""
    from app.models.team import Team

    team = Team(name=f"team-{uuid.uuid4().hex[:8]}", leader_id=None)
    session.add(team)  # type: ignore[attr-defined]
    await session.flush()  # type: ignore[attr-defined]
    return team.id
