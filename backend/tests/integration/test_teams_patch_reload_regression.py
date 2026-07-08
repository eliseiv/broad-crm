"""Регрессионный тест PATCH /api/teams/{id}: ответ отражает СВЕЖИЙ состав, не устаревший.

Прод-баг (исправлен `populate_existing=True` в `TeamRepository.get_with_members`):
после смены состава через `PATCH` тело ответа отдавало УСТАРЕВШУЮ коллекцию
`members`/`member_count`/`leader_username` (колонка `leader_id` была верна, БД —
корректна). Причина — identity-map SQLAlchemy: сессия сконфигурирована
`expire_on_commit=False` (app/db.py), поэтому после `commit()` уже загруженная
`selectinload`-коллекция `Team.members` НЕ инвалидируется; `replace_members` пишет
`user_teams` Core-statements'ами в обход ORM-relationship, а повторный
`get_with_members` без `populate_existing` возвращает тот же instance со СТАРЫМ составом.

Фейк-репозиторий (`conftest.RbacFakeDb`) этот класс багов воспроизвести НЕ может (его
`get_with_members` всегда отдаёт живой мутированный объект), поэтому регрессия проверяется
на РЕАЛЬНОМ Postgres — собственный async-engine + sessionmaker с той же семантикой, что и
прод (`expire_on_commit=False`, `autoflush=False`). БД предоставляет CI (`postgres:16`,
`DATABASE_URL`, см. .github/workflows/ci.yml); локально — эквивалентный одноразовый
контейнер `postgres:16`.

Инвариант всех кейсов: тело ответа PATCH == свежий GET по
`members`/`member_count`/`leader_id`/`leader_username`. Сверка с 04-api.md#teams
(«Response 200 — обновлённый TeamListItem», авто-передача/авто-назначение) и ADR-026.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from app.models import Base
from app.models.role import Role
from app.models.user import User
from app.repositories.team_repository import TeamRepository
from app.repositories.user_repository import UserRepository
from app.schemas.team import TeamCreateRequest, TeamListItem, TeamUpdateRequest
from app.services.team_service import TeamService
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Реальный URL БД захватывается на импорте модуля (до autouse-фикстуры conftest,
# которая monkeypatch'ит DATABASE_URL на фейковый для DB-less фейк-тестов). CI задаёт
# job-level DATABASE_URL; локально — экспортируется на запуск (одноразовый postgres:16).
_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


@asynccontextmanager
async def _sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Async-engine + sessionmaker поверх реального Postgres (семантика как в app/db.py).

    Схема создаётся идемпотентно (`create_all`, checkfirst). `expire_on_commit=False` —
    обязательное условие воспроизведения бага (иначе commit истёк бы коллекцию сам).
    """
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — регрессионный тест PATCH teams "
            "требует реального Postgres (CI поднимает postgres:16; локально — контейнер)."
        )
    engine = create_async_engine(_DB_URL, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


def _service(session: AsyncSession) -> TeamService:
    return TeamService(teams=TeamRepository(session), users=UserRepository(session))


async def _seed_users(
    sm: async_sessionmaker[AsyncSession], count: int
) -> list[tuple[uuid.UUID, str]]:
    """Создаёт роль и `count` пользователей; возвращает [(id, username)] в порядке создания."""
    async with sm() as session:
        role = Role(name=f"роль-{uuid.uuid4().hex[:8]}", permissions={"teams": ["view"]})
        session.add(role)
        await session.flush()
        users = [
            User(
                username=f"u-{uuid.uuid4().hex[:10]}",
                role_id=role.id,
                password_hash="x",
                is_active=True,
            )
            for _ in range(count)
        ]
        session.add_all(users)
        await session.commit()
        return [(user.id, user.username) for user in users]


async def _create_team(
    sm: async_sessionmaker[AsyncSession],
    *,
    leader_id: uuid.UUID | None,
    member_ids: list[uuid.UUID],
) -> uuid.UUID:
    async with sm() as session:
        item = await _service(session).create_team(
            TeamCreateRequest(
                name=f"Команда-{uuid.uuid4().hex[:8]}",
                leader_id=leader_id,
                member_ids=member_ids,
            )
        )
        return item.id


async def _patch_team(
    sm: async_sessionmaker[AsyncSession], team_id: uuid.UUID, payload: TeamUpdateRequest
) -> TeamListItem:
    """Отдельная сессия (как отдельный HTTP-запрос) — путь load→mutate→reload бага."""
    async with sm() as session:
        return await _service(session).update_team(team_id, payload)


async def _fresh_get(sm: async_sessionmaker[AsyncSession], team_id: uuid.UUID) -> TeamListItem:
    """Свежий GET в НОВОЙ сессии (чистая identity-map) — эталон актуального состояния."""
    async with sm() as session:
        response = await _service(session).list_teams()
    return next(item for item in response.items if item.id == team_id)


def _state(item: TeamListItem) -> tuple[frozenset[str], int, str | None, str | None]:
    """Ключ сравнения: состав/счётчик/лидер (id + username) — поля, где жила регрессия."""
    return (
        frozenset(str(member.id) for member in item.members),
        item.member_count,
        str(item.leader_id) if item.leader_id is not None else None,
        item.leader_username,
    )


@pytest.mark.asyncio
async def test_patch_excluding_leader_response_reflects_fresh_members_not_stale() -> None:
    """Кейс 1 (ядро регрессии): команда [A,B] лидер A → PATCH member_ids=[B].

    Ответ ДОЛЖЕН отражать только B (member_count=1, leader=B — авто-передача, ADR-026),
    а НЕ устаревшие [A,B]/count=2/leader=A. И совпадать со свежим GET.
    """
    async with _sessionmaker() as sm:
        (a_id, a_name), (b_id, b_name) = await _seed_users(sm, 2)
        team_id = await _create_team(sm, leader_id=a_id, member_ids=[a_id, b_id])

        patched = await _patch_team(sm, team_id, TeamUpdateRequest(member_ids=[b_id]))
        fresh = await _fresh_get(sm, team_id)

    # Регрессия: ответ PATCH не должен быть устаревшим (== свежий GET).
    assert _state(patched) == _state(fresh)
    # Конкретика бага — только B, а не [A,B]/count 2/leader A.
    assert {str(m.id) for m in patched.members} == {str(b_id)}
    assert patched.member_count == 1
    assert str(patched.leader_id) == str(b_id)
    assert patched.leader_username == b_name
    assert str(a_id) not in {str(m.id) for m in patched.members}
    assert str(patched.leader_id) != str(a_id)
    assert patched.leader_username != a_name


@pytest.mark.asyncio
async def test_patch_explicit_leader_and_members_response_is_post_state() -> None:
    """Кейс 2: явная смена leader_id + новый member_ids → ответ == пост-операционное состояние."""
    async with _sessionmaker() as sm:
        (a_id, _a_name), (b_id, b_name), (c_id, c_name) = await _seed_users(sm, 3)
        team_id = await _create_team(sm, leader_id=a_id, member_ids=[a_id, b_id])

        patched = await _patch_team(
            sm, team_id, TeamUpdateRequest(leader_id=c_id, member_ids=[b_id, c_id])
        )
        fresh = await _fresh_get(sm, team_id)

    assert _state(patched) == _state(fresh)
    assert {str(m.id) for m in patched.members} == {str(b_id), str(c_id)}
    assert patched.member_count == 2
    assert str(patched.leader_id) == str(c_id)
    assert patched.leader_username == c_name
    assert b_name in {m.username for m in patched.members}


@pytest.mark.asyncio
async def test_patch_add_members_to_leaderless_team_auto_assigns_first() -> None:
    """Кейс 3: добавление участников в команду без лидера → авто-назначение первого (ADR-026)."""
    async with _sessionmaker() as sm:
        (a_id, a_name), (b_id, _b_name) = await _seed_users(sm, 2)
        team_id = await _create_team(sm, leader_id=None, member_ids=[])

        patched = await _patch_team(sm, team_id, TeamUpdateRequest(member_ids=[a_id, b_id]))
        fresh = await _fresh_get(sm, team_id)

    assert _state(patched) == _state(fresh)
    assert {str(m.id) for m in patched.members} == {str(a_id), str(b_id)}
    assert patched.member_count == 2
    assert str(patched.leader_id) == str(a_id)
    assert patched.leader_username == a_name


@pytest.mark.asyncio
async def test_patch_clear_leader_response_reflects_null_leader() -> None:
    """Кейс 4: leader_id=null (снятие лидера, состав не меняется) → ответ с leader_id=null."""
    async with _sessionmaker() as sm:
        (a_id, _a_name), (b_id, _b_name) = await _seed_users(sm, 2)
        team_id = await _create_team(sm, leader_id=a_id, member_ids=[a_id, b_id])

        patched = await _patch_team(sm, team_id, TeamUpdateRequest(leader_id=None))
        fresh = await _fresh_get(sm, team_id)

    assert _state(patched) == _state(fresh)
    assert patched.leader_id is None
    assert patched.leader_username is None
    # Снятие лидера НЕ трогает состав (member_ids не передан).
    assert {str(m.id) for m in patched.members} == {str(a_id), str(b_id)}
    assert patched.member_count == 2
