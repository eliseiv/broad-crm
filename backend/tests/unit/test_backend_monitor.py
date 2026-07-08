"""Unit-тесты монитора бэков check_one/poll_once/_check_snapshot/run (ADR-020/024).

Репозиторий, сессия БД и проверка доступности — стабы (без сети/БД). Покрывают:
grace-порог алерта (🔴 только после непрерывной недоступности ≥ порога, ADR-024),
recovery только если 🔴 был, overall-deadline (`asyncio.wait_for` прерывает зависшую
проверку → «Таймаут подключения»), гейт Telegram (`telegram=None` → не отправляем,
статус пишем), устойчивость check_one/run к исключениям. Исхода `unknown` у бэков НЕТ.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.infra.backend_check import REASON_TIMEOUT, BackendCheckResult
from app.models.service_backend import BackendStatus
from app.services import backend_monitor_service as mod
from app.services.backend_monitor_service import BackendMonitorService


class _FakeBackend:
    def __init__(
        self,
        *,
        status: str = BackendStatus.pending.value,
        error_since: datetime | None = None,
        alert_sent: bool = False,
    ) -> None:
        self.id = uuid.uuid4()
        self.code = "api-eu"
        self.name = "API EU"
        self.domain = "api.example.com"
        self.check_status = status
        self.error_since = error_since
        self.alert_sent = alert_sent


class _FakeRepo:
    def __init__(self, backend: _FakeBackend | None) -> None:
        self.backend = backend
        self.updates: list[dict[str, Any]] = []

    async def get_by_id(self, backend_id: uuid.UUID) -> _FakeBackend | None:
        if self.backend is not None and backend_id == self.backend.id:
            return self.backend
        return None

    async def list_all(self) -> list[_FakeBackend]:
        return [self.backend] if self.backend is not None else []

    async def update_check(
        self,
        backend_id: uuid.UUID,
        *,
        status: str,
        error_message: str | None,
        last_checked_at: object,
        error_since: datetime | None,
        alert_sent: bool,
    ) -> None:
        self.updates.append(
            {
                "id": backend_id,
                "status": status,
                "error_message": error_message,
                "error_since": error_since,
                "alert_sent": alert_sent,
            }
        )


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        return None


class _FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> bool:
        self.sent.append(text)
        return True


def _settings() -> object:
    from app.config import get_settings

    return get_settings()


def _make_service(
    monkeypatch: pytest.MonkeyPatch,
    repo: _FakeRepo,
    *,
    telegram: _FakeTelegram | None,
    outcome: BackendCheckResult,
    captured: dict[str, object] | None = None,
) -> BackendMonitorService:
    monkeypatch.setattr(mod, "BackendRepository", lambda _session: repo)

    async def _fake_check_backend(domain: str) -> BackendCheckResult:
        if captured is not None:
            captured["domain"] = domain
        return outcome

    monkeypatch.setattr(mod, "check_backend", _fake_check_backend)

    return BackendMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- check_one
async def test_check_one_working_updates_db_no_telegram_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend(status=BackendStatus.pending.value)
    repo = _FakeRepo(backend)
    svc = _make_service(
        monkeypatch, repo, telegram=None, outcome=BackendCheckResult("working", None)
    )

    await svc.check_one(backend.id)

    assert repo.updates[0]["status"] == BackendStatus.working.value
    assert repo.updates[0]["error_message"] is None


async def test_check_one_builds_url_from_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(status=BackendStatus.working.value)
    repo = _FakeRepo(backend)
    captured: dict[str, object] = {}
    svc = _make_service(
        monkeypatch,
        repo,
        telegram=None,
        outcome=BackendCheckResult("working", None),
        captured=captured,
    )

    await svc.check_one(backend.id)

    assert captured["domain"] == "api.example.com"


async def test_check_one_first_error_starts_grace_no_red(monkeypatch: pytest.MonkeyPatch) -> None:
    # Первый провал (working→error): статус error немедленно, но 🔴 НЕ шлём (grace, ADR-024).
    backend = _FakeBackend(status=BackendStatus.working.value)
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("error", "Бэк недоступен")
    )

    await svc.check_one(backend.id)

    assert repo.updates[0]["status"] == BackendStatus.error.value
    assert repo.updates[0]["error_since"] is not None  # начало эпизода зафиксировано
    assert repo.updates[0]["alert_sent"] is False
    assert tg.sent == []  # grace-окно не истекло → тихо


async def test_check_one_error_after_grace_sends_red(monkeypatch: pytest.MonkeyPatch) -> None:
    # Недоступен непрерывно > 30 мин (error_since в прошлом), 🔴 ещё не слали → отправить 🔴.
    past = datetime.now(UTC) - timedelta(minutes=31)
    backend = _FakeBackend(status=BackendStatus.error.value, error_since=past, alert_sent=False)
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("error", "Бэк недоступен")
    )

    await svc.check_one(backend.id)

    assert repo.updates[0]["alert_sent"] is True
    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
    assert 'Бэк "API EU" [api-eu] api.example.com' in tg.sent[0]
    assert 'Бэк не работает: "Бэк недоступен"' in tg.sent[0]


async def test_check_one_recovery_sends_green_when_alert_was_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend(
        status=BackendStatus.error.value,
        error_since=datetime.now(UTC) - timedelta(minutes=40),
        alert_sent=True,
    )
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("working", None))

    await svc.check_one(backend.id)

    assert repo.updates[0]["status"] == BackendStatus.working.value
    assert repo.updates[0]["error_since"] is None
    assert repo.updates[0]["alert_sent"] is False
    assert len(tg.sent) == 1
    assert "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢" in tg.sent[0]
    assert "Бэк снова работает" in tg.sent[0]


async def test_check_one_recovery_silent_when_alert_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Рестарт < 30 мин: 🔴 не слали → 🟢 не нужен (тихо), статус всё равно working.
    backend = _FakeBackend(
        status=BackendStatus.error.value,
        error_since=datetime.now(UTC) - timedelta(minutes=5),
        alert_sent=False,
    )
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("working", None))

    await svc.check_one(backend.id)

    assert repo.updates[0]["status"] == BackendStatus.working.value
    assert tg.sent == []


async def test_check_one_red_suppressed_when_telegram_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    past = datetime.now(UTC) - timedelta(minutes=31)
    backend = _FakeBackend(status=BackendStatus.error.value, error_since=past, alert_sent=False)
    repo = _FakeRepo(backend)
    svc = _make_service(
        monkeypatch, repo, telegram=None, outcome=BackendCheckResult("error", "Таймаут подключения")
    )

    await svc.check_one(backend.id)

    # Статус/alert_sent пишутся, но отправлять некуда (бот отключён) — исключений нет.
    assert repo.updates[0]["status"] == BackendStatus.error.value
    assert repo.updates[0]["alert_sent"] is True


async def test_check_one_deadline_aborts_hung_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # Проверка «зависает» → overall-deadline (asyncio.wait_for) прерывает её →
    # гарантированно конклюзивный error «Таймаут подключения» (ADR-024).
    monkeypatch.setenv("BACKEND_CHECK_DEADLINE_SEC", "0.05")
    from app.config import get_settings

    get_settings.cache_clear()

    backend = _FakeBackend(status=BackendStatus.working.value)
    repo = _FakeRepo(backend)
    monkeypatch.setattr(mod, "BackendRepository", lambda _session: repo)

    async def _hang(_domain: str) -> BackendCheckResult:
        await asyncio.Event().wait()  # никогда не завершится
        raise AssertionError("unreachable")

    monkeypatch.setattr(mod, "check_backend", _hang)

    svc = BackendMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=None,
        settings=get_settings(),
    )

    await svc.check_one(backend.id)

    assert repo.updates[0]["status"] == BackendStatus.error.value
    assert repo.updates[0]["error_message"] == REASON_TIMEOUT


async def test_check_one_working_to_working_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(status=BackendStatus.working.value)
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("working", None))

    await svc.check_one(backend.id)

    assert repo.updates[0]["status"] == BackendStatus.working.value
    assert tg.sent == []


async def test_check_one_missing_backend_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeRepo(None)
    svc = _make_service(
        monkeypatch, repo, telegram=None, outcome=BackendCheckResult("working", None)
    )

    await svc.check_one(uuid.uuid4())

    assert repo.updates == []


async def test_check_one_swallows_exception_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class _RecordingLogger:
        def error(self, event: str, **_kw: object) -> None:
            events.append(event)

        def warning(self, event: str, **_kw: object) -> None:
            events.append(event)

        def info(self, event: str, **_kw: object) -> None:
            events.append(event)

    monkeypatch.setattr(mod, "logger", _RecordingLogger())

    class _BoomSession:
        async def __aenter__(self) -> _BoomSession:
            raise RuntimeError("db down")

        async def __aexit__(self, *exc: object) -> bool:
            return False

    svc = BackendMonitorService(
        sessionmaker=lambda: _BoomSession(),  # type: ignore[arg-type]
        telegram=None,
        settings=_settings(),  # type: ignore[arg-type]
    )

    await svc.check_one(uuid.uuid4())

    assert "backend_check_one_failed" in events


# ---------------------------------------------------------------- poll_once
async def test_poll_once_empty_registry_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeRepo(None)
    svc = _make_service(
        monkeypatch, repo, telegram=None, outcome=BackendCheckResult("working", None)
    )

    await svc.poll_once()

    assert repo.updates == []


async def test_poll_once_checks_all_backends_and_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(status=BackendStatus.working.value)
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch,
        repo,
        telegram=tg,
        outcome=BackendCheckResult("error", "Ошибка бэка (HTTP 500)"),
    )

    await svc.poll_once()

    assert repo.updates[0]["status"] == BackendStatus.error.value
    assert repo.updates[0]["error_message"] == "Ошибка бэка (HTTP 500)"
    # Первый провал → grace, 🔴 не шлём.
    assert tg.sent == []


# ---------------------------------------------------------------- run()
async def test_run_handles_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = BackendMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=None,
        settings=_settings(),  # type: ignore[arg-type]
    )
    calls: list[int] = []

    async def fake_poll() -> None:
        calls.append(1)

    async def fake_sleep(_d: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(svc, "poll_once", fake_poll)
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await svc.run()

    assert calls == [1]


async def test_run_survives_iteration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = BackendMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=None,
        settings=_settings(),  # type: ignore[arg-type]
    )
    state = {"n": 0}

    async def fake_poll() -> None:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("boom")

    async def fake_sleep(_d: float) -> None:
        if state["n"] >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(svc, "poll_once", fake_poll)
    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await svc.run()

    assert state["n"] == 2
