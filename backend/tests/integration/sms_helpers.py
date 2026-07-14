"""Общая инфраструктура integration-тестов модуля «СМС» (реальный Postgres, ADR-030).

Модуль вспомогательный (без `test_`-префикса — pytest его не коллектит). Даёт:
- `sms_db()` — async-engine + sessionmaker поверх реального Postgres (create_all +
  TRUNCATE на входе для изоляции; семантика как app/db.py: expire_on_commit=False);
- `build_app()` — FastAPI-приложение с override `get_session` (тест-сессия) и
  `get_current_principal` (инъекция принципала); scope считается по РЕАЛЬНОМУ user_teams;
- `build_principal()` / seed-хелперы (роль/пользователь/команда/членство/номер/SMS/линк).

Реальный URL БД захватывается на импорте (до autouse-фикстуры conftest, monkeypatch'ащей
DATABASE_URL на фейковый). CI задаёт DATABASE_URL (postgres:16); локально — TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from app.models import Base
from app.models.role import Role
from app.models.sms_delivery import SmsDelivery
from app.models.sms_inbound import SmsInbound
from app.models.sms_phone_number import SmsPhoneNumber
from app.models.sms_telegram_link import SmsTelegramLink
from app.models.team import Team, user_teams
from app.models.user import User
from app.models.user_channel_team import user_channel_teams
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

# Очищаемые между тестами таблицы (CASCADE снимает и зависимые sms_deliveries и т.п.).
# servers/proxies/ai_keys — для reveal-тестов (ADR-035): их list-таблицы имеют
# уникальные ограничения (servers.ip), очистка исключает коллизии между тестами.
_TRUNCATE = (
    "sms_deliveries, sms_telegram_links, sms_inbound, sms_phone_numbers, "
    "user_teams, teams, users, roles, servers, proxies, ai_keys, backends"
)


@asynccontextmanager
async def sms_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessionmaker поверх реального Postgres; чистая схема + TRUNCATE для изоляции."""
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — integration-тесты SMS требуют "
            "реального Postgres (CI поднимает postgres:16; локально — контейнер)."
        )
    engine = create_async_engine(_DB_URL, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(sa_text(f"TRUNCATE {_TRUNCATE} RESTART IDENTITY CASCADE"))
        sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        await _bootstrap_superadmin_anchor(sm)
        yield sm
    finally:
        await engine.dispose()


async def _bootstrap_superadmin_anchor(sm: async_sessionmaker[AsyncSession]) -> None:
    """Сеет системную строку-якорь супер-админа — СРАЗУ после `create_all`/TRUNCATE.

    Нормативно (ADR-051 §1.3): в тестах `lifespan` не выполняется ⇒ единственного писателя
    якоря (`UserRepository.ensure_superadmin_anchor`) обязана вызвать фикстура. Метод
    владеет своей транзакцией и коммитит сам ⇒ ему передаётся ОТДЕЛЬНАЯ сессия.

    ⚠️ Побочный эффект (ADR-051 §1.1, шаг (3)): роли `admin` в фикстурах нет ⇒ bootstrap
    создаёт её сам, и она присутствует в КАЖДОЙ интеграционной тестовой БД.
    """
    from app.repositories.user_repository import UserRepository

    async with sm() as session:
        await UserRepository(session).ensure_superadmin_anchor()


def build_principal(
    *,
    user_id: uuid.UUID | None = None,
    is_superadmin: bool = True,
    role: str = "admin",
    permissions: dict[str, list[str]] | None = None,
    mail_includes_unassigned: bool = False,
    sms_includes_unassigned: bool = False,
) -> Any:
    """Строит `Principal` с идентичностью (`user_id` не-опционален — ADR-051 §1.2).

    Супер-админ без явного `user_id` → константа `SUPERADMIN_USER_ID` (системная
    строка-якорь), как и в проде (`get_current_principal`). Наличие идентичности НЕ даёт
    якорю Telegram-привязку: `POST /api/sms/telegram/link` под супер-админом — по-прежнему
    `403`, но по security-основанию (ADR-051 §1.6), а не «нет `user_id`».
    """
    from app.api.deps import Principal
    from app.domain.permissions import full_catalog_permissions
    from app.domain.superadmin import SUPERADMIN_USER_ID

    if user_id is None:
        user_id = SUPERADMIN_USER_ID if is_superadmin else uuid.uuid4()

    return Principal(
        username="tester",
        role=role,
        permissions=full_catalog_permissions() if permissions is None else permissions,
        is_superadmin=is_superadmin,
        user_id=user_id,
        mail_includes_unassigned=mail_includes_unassigned,
        sms_includes_unassigned=sms_includes_unassigned,
    )


def build_app(
    sm: async_sessionmaker[AsyncSession],
    principal: Any,
    *,
    overrides: dict[Any, Callable[[], Any]] | None = None,
) -> Any:
    """Приложение с тест-сессией и инъекцией принципала (+доп. dependency-override)."""
    from app.api import deps
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sm() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_current_principal] = lambda: principal
    for dep, factory in (overrides or {}).items():
        app.dependency_overrides[dep] = factory
    return app


def client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- seed-хелперы -----------------------------------------------------------


async def seed_role(
    session: AsyncSession,
    *,
    name: str | None = None,
    permissions: dict[str, list[str]] | None = None,
) -> Role:
    role = Role(
        name=name or f"role-{uuid.uuid4().hex[:8]}",
        permissions=permissions or {"sms": ["view"]},
    )
    session.add(role)
    await session.flush()
    return role


async def seed_user(
    session: AsyncSession,
    role: Role,
    *,
    username: str | None = None,
    telegram: str | None = None,
    is_active: bool = True,
    first_login_at: datetime | None = None,
) -> User:
    user = User(
        username=username or f"user-{uuid.uuid4().hex[:10]}",
        role_id=role.id,
        password_hash="x",
        is_active=is_active,
        telegram=telegram,
        first_login_at=first_login_at,
    )
    session.add(user)
    await session.flush()
    return user


async def seed_team(session: AsyncSession, *, name: str | None = None) -> Team:
    team = Team(name=name or f"team-{uuid.uuid4().hex[:8]}", leader_id=None)
    session.add(team)
    await session.flush()
    return team


async def add_membership(session: AsyncSession, user_id: uuid.UUID, team_id: uuid.UUID) -> None:
    await session.execute(insert(user_teams).values(user_id=user_id, team_id=team_id))


async def add_extra_team(
    session: AsyncSession, user_id: uuid.UUID, channel: str, team_id: uuid.UUID
) -> None:
    """Строка `user_channel_teams` — ДОП-команда канала (ADR-055 §2.1).

    Хранится только добавка: базовые команды (`user_teams`) сюда не пишутся — инвариант
    нормализации §2.3 обеспечивают сервисы users/teams. Прямая вставка позволяет тесту
    воспроизвести и «висящую» добавку, которую этот инвариант обязан снять.
    """
    await session.execute(
        insert(user_channel_teams).values(user_id=user_id, channel=channel, team_id=team_id)
    )


async def seed_number(
    session: AsyncSession,
    *,
    phone_number: str,
    team_id: uuid.UUID | None = None,
    label: str | None = None,
    login: str | None = None,
    app_name: str | None = None,
    note: str | None = None,
) -> SmsPhoneNumber:
    number = SmsPhoneNumber(
        phone_number=phone_number,
        team_id=team_id,
        label=label,
        login=login,
        app_name=app_name,
        note=note,
    )
    session.add(number)
    await session.flush()
    return number


async def seed_inbound(
    session: AsyncSession,
    *,
    from_number: str,
    to_number: str,
    body: str = "текст",
    team_id: uuid.UUID | None = None,
    twilio_message_sid: str | None = None,
    received_at: datetime | None = None,
) -> SmsInbound:
    sms = SmsInbound(
        twilio_message_sid=twilio_message_sid,
        from_number=from_number,
        to_number=to_number,
        body=body,
        team_id=team_id,
        raw_payload={"From": from_number, "To": to_number},
        received_at=received_at or datetime.now(UTC),
    )
    session.add(sms)
    await session.flush()
    return sms


async def seed_link(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    user_id: uuid.UUID,
    dead_at: datetime | None = None,
) -> SmsTelegramLink:
    link = SmsTelegramLink(telegram_user_id=telegram_user_id, user_id=user_id, dead_at=dead_at)
    session.add(link)
    await session.flush()
    return link


async def seed_delivery(
    session: AsyncSession,
    *,
    inbound_sms_id: int,
    user_id: uuid.UUID,
    telegram_user_id: int,
    status: str = "pending",
    attempts: int = 0,
) -> SmsDelivery:
    delivery = SmsDelivery(
        inbound_sms_id=inbound_sms_id,
        user_id=user_id,
        telegram_user_id=telegram_user_id,
        status=status,
        attempts=attempts,
    )
    session.add(delivery)
    await session.flush()
    return delivery


class FakeBot:
    """Фейк SmsBotClient: записывает отправки; программируемое поведение по chat_id."""

    def __init__(self, *, is_configured: bool = True) -> None:
        self.is_configured = is_configured
        self.sent: list[tuple[int, str]] = []
        self._raise_forbidden: set[int] = set()
        self._raise_api_error: set[int] = set()

    def forbidden_for(self, chat_id: int) -> None:
        self._raise_forbidden.add(chat_id)

    def api_error_for(self, chat_id: int) -> None:
        self._raise_api_error.add(chat_id)

    async def send_message(
        self, chat_id: int, text: str, *, reply_markup: Any | None = None
    ) -> dict[str, Any]:
        from app.infra.sms_telegram import TelegramApiError, TelegramForbiddenError

        if chat_id in self._raise_forbidden:
            raise TelegramForbiddenError("forbidden")
        if chat_id in self._raise_api_error:
            raise TelegramApiError("api error")
        self.sent.append((chat_id, text))
        return {"ok": True}
