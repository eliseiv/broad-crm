"""Unit-тесты конфигурации почты S4 (ADR-044 §6/§9): fail-fast push-ботов + lifespan-гейт.

- `Settings` fail-fast на старте: невалидный UUID / дубликат `MAIL_BOT_*_TEAM_ID` →
  ValueError (§9). Валидная тройка token+secret+team_id → бот сконфигурирован.
- Lifespan диспетчера: при `MAIL_DISPATCH_ENABLED=false` фоновая задача НЕ создаётся
  (`MailDispatcherService` не инстанцируется), при `true` — создаётся.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.config import Settings

_UUID_A = "11111111-1111-1111-1111-111111111111"
_UUID_B = "22222222-2222-2222-2222-222222222222"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "admin_user": "admin",
        "admin_password": "secret",
        "jwt_secret": "test-secret-with-more-than-32-bytes-long",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --- fail-fast push-ботов ----------------------------------------------------
def test_invalid_team_id_uuid_fails_fast() -> None:
    with pytest.raises(ValueError, match="TEAM_ID"):
        _settings(
            mail_bot_ivan_token="t",
            mail_bot_ivan_webhook_secret="s",
            mail_bot_ivan_team_id="not-a-uuid",
        )


def test_duplicate_team_id_fails_fast() -> None:
    with pytest.raises(ValueError, match="Дубликат"):
        _settings(
            mail_bot_ivan_token="t1",
            mail_bot_ivan_webhook_secret="s1",
            mail_bot_ivan_team_id=_UUID_A,
            mail_bot_alexandra_token="t2",
            mail_bot_alexandra_webhook_secret="s2",
            mail_bot_alexandra_team_id=_UUID_A,
        )


def test_two_distinct_bots_configured() -> None:
    settings = _settings(
        mail_bot_ivan_token="t1",
        mail_bot_ivan_webhook_secret="s1",
        mail_bot_ivan_team_id=_UUID_A,
        mail_bot_alexandra_token="t2",
        mail_bot_alexandra_webhook_secret="s2",
        mail_bot_alexandra_team_id=_UUID_B,
    )
    bots = settings.mail_push_bots
    assert {b.name for b in bots} == {"ivan", "alexandra"}
    assert {b.team_id for b in bots} == {uuid.UUID(_UUID_A), uuid.UUID(_UUID_B)}


def test_partial_bot_env_skipped() -> None:
    # Токен есть, а секрет/team_id нет → бот НЕ считается сконфигурированным (не падает).
    settings = _settings(mail_bot_ivan_token="only-token")
    assert settings.mail_push_bots == []


# --- lifespan-гейт диспетчера ------------------------------------------------
class _DummyTask:
    """Стаб asyncio.Task: cancel/await — no-op (нейтрализует фоновые задачи lifespan)."""

    def cancel(self) -> None:
        return None

    def __await__(self):  # type: ignore[no-untyped-def]
        async def _noop() -> None:
            return None

        return _noop().__await__()


async def _run_lifespan_and_capture(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> bool:
    """Запускает lifespan приложения, возвращает, был ли инстанцирован диспетчер."""
    import app.main as main_mod
    from app.config import get_settings

    constructed = {"dispatcher": False}

    class _SpyDispatcher:
        def __init__(self, **_kwargs: Any) -> None:
            constructed["dispatcher"] = True

        async def run(self) -> None:  # pragma: no cover - не запускается (create_task застаблен)
            return None

    def _fake_create_task(coro: Any) -> _DummyTask:
        coro.close()  # закрыть корутину — не планировать реальную фоновую работу
        return _DummyTask()

    monkeypatch.setattr(main_mod, "MailDispatcherService", _SpyDispatcher)
    monkeypatch.setattr(main_mod.asyncio, "create_task", _fake_create_task)
    monkeypatch.setenv("MAIL_DISPATCH_ENABLED", "true" if enabled else "false")
    get_settings.cache_clear()

    app = main_mod.create_app(get_settings())
    async with app.router.lifespan_context(app):
        pass
    get_settings.cache_clear()
    return constructed["dispatcher"]


async def test_dispatcher_not_created_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    created = await _run_lifespan_and_capture(monkeypatch, enabled=False)
    assert created is False


async def test_dispatcher_created_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    created = await _run_lifespan_and_capture(monkeypatch, enabled=True)
    assert created is True
