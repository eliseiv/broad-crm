"""Инфраструктура integration-тестов почты S3/S4 (реальный Postgres, ADR-044).

Вспомогательный модуль (без `test_`-префикса — pytest не коллектит). В отличие от
`mail_helpers.py` (S1/S2, чистые репозитории), здесь поднимается FastAPI-app
(`create_app`) с override `get_session`/`get_current_principal`, инъекцией фейкового
`MailClient` в сервис почты (агрегатор не вызывается вживую) и seed-хелперами каталога/
писем/тегов/линков/уведомлений.

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
from app.models.mail_account import MailAccount
from app.models.mail_message import MailMessage
from app.models.mail_tag import MailTag, MailTagRule
from app.models.mail_telegram import MailTelegramLink, MailTelegramNotification
from app.models.mail_user_settings import MailUserSettings
from app.models.role import Role
from app.models.team import Team, user_teams
from app.models.user import User
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_DB_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""

_TRUNCATE = (
    "mail_message_tags, mail_tag_rules, mail_tags, mail_telegram_notifications, "
    "mail_telegram_links, mail_user_settings, mail_sent_messages, mail_messages, "
    "mail_accounts, user_teams, teams, users, roles"
)


@asynccontextmanager
async def mail_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessionmaker поверх реального Postgres; чистая схема + TRUNCATE для изоляции."""
    if not _DB_URL:
        pytest.fail(
            "DATABASE_URL/TEST_DATABASE_URL не задан — integration-тесты почты требуют "
            "реального Postgres (CI поднимает postgres:16; локально — контейнер)."
        )
    engine = create_async_engine(_DB_URL, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(sa_text(f"TRUNCATE {_TRUNCATE} RESTART IDENTITY CASCADE"))
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


# --- FakeMailClient: граница к агрегатору (не вызывается вживую) --------------


class FakeMailClient:
    """Фейк `MailClient`: записывает вызовы, отдаёт программируемые ответы/ошибки.

    По умолчанию `create_mailbox` возвращает `{id: next_id, is_active: true}`. Любой
    метод можно заставить бросить `MailUnavailable`/`MailRejected(status)` через
    `fail_with`. `create_should_raise_catalog` симулирует падение вставки каталога.
    """

    def __init__(self, *, new_id: int = 1000) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._new_id = new_id
        self._responses: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, Exception] = {}

    def set_response(self, method: str, payload: dict[str, Any]) -> None:
        self._responses[method] = payload

    def fail_with(self, method: str, error: Exception) -> None:
        self._errors[method] = error

    def _record(self, method: str, *args: Any) -> None:
        self.calls.append((method, args))
        if method in self._errors:
            raise self._errors[method]

    async def test_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._record("test_mailbox", payload)
        return self._responses.get("test_mailbox", {"ok": True})

    async def create_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._record("create_mailbox", payload)
        return self._responses.get("create_mailbox", {"id": self._new_id, "is_active": True})

    async def update_mailbox(self, mailbox_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._record("update_mailbox", mailbox_id, payload)
        return self._responses.get("update_mailbox", {})

    async def delete_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        self._record("delete_mailbox", mailbox_id)
        return self._responses.get("delete_mailbox", {})

    async def sync_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        self._record("sync_mailbox", mailbox_id)
        return self._responses.get("sync_mailbox", {"queued": True})

    async def authorize_oauth(self, crm_state: str) -> dict[str, Any]:
        self._record("authorize_oauth", crm_state)
        return self._responses.get(
            "authorize_oauth",
            {"authorize_url": "https://login.microsoftonline.com/consumers/authorize?x=1"},
        )

    async def send_message(self, mailbox_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self._record("send_message", mailbox_id, payload)
        return self._responses.get(
            "send_message", {"sent_id": 1, "smtp_message_id": "<smtp-1@example.com>"}
        )


def build_principal(
    *,
    user_id: uuid.UUID | None = None,
    is_superadmin: bool = True,
    role: str = "admin",
    permissions: dict[str, list[str]] | None = None,
) -> Any:
    """Строит `Principal` с явным `user_id` (для scope/привязки)."""
    from app.api.deps import Principal
    from app.domain.permissions import full_catalog_permissions

    return Principal(
        username="tester",
        role=role,
        permissions=full_catalog_permissions() if permissions is None else permissions,
        is_superadmin=is_superadmin,
        user_id=user_id,
    )


def build_app(
    sm: async_sessionmaker[AsyncSession],
    principal: Any,
    *,
    mail_client: Any | None = None,
    overrides: dict[Any, Callable[[], Any]] | None = None,
) -> Any:
    """Приложение с тест-сессией, инъекцией принципала и фейкового MailClient."""
    from app.api import deps
    from app.config import get_settings
    from app.main import create_app
    from app.services.mail_service import MailService
    from fastapi import Depends

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

    if mail_client is not None:

        def _mail_service(
            session: AsyncSession = Depends(deps.get_session),  # noqa: B008
            settings: Any = Depends(deps.get_settings_dep),  # noqa: B008
        ) -> MailService:
            return MailService(session=session, client=mail_client, settings=settings)

        app.dependency_overrides[deps.get_mail_service] = _mail_service

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
        permissions=permissions or {"mail": ["view"]},
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


async def seed_account(
    session: AsyncSession,
    *,
    account_id: int,
    email: str | None = None,
    number: str | None = None,
    app_name: str | None = None,
    display_name: str | None = None,
    team_id: uuid.UUID | None = None,
    is_active: bool = True,
    last_sync_error: str | None = None,
    down_alert_sent_at: datetime | None = None,
) -> MailAccount:
    account = MailAccount(
        id=account_id,
        email=email or f"box{account_id}@example.com",
        number=number,
        app_name=app_name,
        display_name=display_name,
        team_id=team_id,
        is_active=is_active,
        last_sync_error=last_sync_error,
        down_alert_sent_at=down_alert_sent_at,
    )
    session.add(account)
    await session.flush()
    return account


async def seed_message(
    session: AsyncSession,
    *,
    account_id: int,
    uid: int,
    uidvalidity: int = 1,
    internal_date: datetime,
    subject: str | None = "Тема",
    from_addr: str = "sender@example.com",
    from_name: str | None = None,
    to_addrs: str = "inbox@example.com",
    cc_addrs: str | None = None,
    body_text: str = "тело",
    body_html: str | None = None,
    message_id_header: str | None = None,
    refs_header: str | None = None,
    notified_at: datetime | None = None,
) -> MailMessage:
    message = MailMessage(
        mail_account_id=account_id,
        uidvalidity=uidvalidity,
        uid=uid,
        subject=subject,
        from_addr=from_addr,
        from_name=from_name,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        internal_date=internal_date,
        body_text=body_text,
        body_html=body_html,
        message_id_header=message_id_header,
        refs_header=refs_header,
        notified_at=notified_at,
    )
    session.add(message)
    await session.flush()
    return message


async def seed_tag(
    session: AsyncSession,
    *,
    name: str,
    color: str = "#123456",
    match_mode: str = "any",
) -> MailTag:
    # Признака «встроенный» у тега больше нет (ADR-047 §1): колонка `is_builtin` дропнута
    # миграцией 0023, удалить можно ЛЮБОЙ тег.
    tag = MailTag(name=name, color=color, match_mode=match_mode)
    session.add(tag)
    await session.flush()
    return tag


async def seed_rule(
    session: AsyncSession, *, tag_id: uuid.UUID, rule_type: str, pattern: str
) -> MailTagRule:
    rule = MailTagRule(tag_id=tag_id, type=rule_type, pattern=pattern)
    session.add(rule)
    await session.flush()
    return rule


async def seed_link(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    user_id: uuid.UUID | None = None,
    username: str | None = None,
    dead_at: datetime | None = None,
) -> MailTelegramLink:
    link = MailTelegramLink(
        telegram_user_id=telegram_user_id,
        user_id=user_id,
        username=username,
        dead_at=dead_at,
    )
    session.add(link)
    await session.flush()
    return link


async def seed_notification(
    session: AsyncSession,
    *,
    message_id: int,
    telegram_user_id: int,
    status: str = "pending",
    attempts: int = 0,
) -> MailTelegramNotification:
    notif = MailTelegramNotification(
        message_id=message_id,
        telegram_user_id=telegram_user_id,
        status=status,
        attempts=attempts,
    )
    session.add(notif)
    await session.flush()
    return notif


async def seed_user_settings(
    session: AsyncSession, *, user_id: uuid.UUID, enabled: bool
) -> MailUserSettings:
    settings_row = MailUserSettings(user_id=user_id, tg_notifications_enabled=enabled)
    session.add(settings_row)
    await session.flush()
    return settings_row


def dt(year: int = 2026, month: int = 1, day: int = 1, hour: int = 0, minute: int = 0) -> datetime:
    """Aware UTC datetime — детерминированный `internal_date` для keyset-тестов."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC)
