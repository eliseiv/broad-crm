"""Unit-тесты монитора бэков check_one/poll_once/_check_snapshot/run (modules/backends).

Репозиторий, сессия БД и проверка доступности — стабы (без сети/БД). Покрывают:
обновление check_status при конклюзивном исходе независимо от Telegram; гейт Telegram
(`telegram=None` → не отправляем, статус всё равно пишем); отправку 🔴/🟢 при переходах;
сборку URL проверки из domain; устойчивость check_one к исключению; устойчивость run к
ошибке итерации и отмене. Исхода `unknown` у бэков НЕТ.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.infra.backend_check import BackendCheckResult
from app.models.service_backend import BackendStatus
from app.services import backend_monitor_service as mod
from app.services.backend_monitor_service import BackendMonitorService


class _FakeBackend:
    def __init__(self, *, status: str = BackendStatus.pending.value) -> None:
        self.id = uuid.uuid4()
        self.code = "api-eu"
        self.name = "API EU"
        self.domain = "api.example.com"
        self.check_status = status


class _FakeRepo:
    def __init__(self, backend: _FakeBackend | None) -> None:
        self.backend = backend
        self.updates: list[tuple[uuid.UUID, str, str | None]] = []

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
    ) -> None:
        self.updates.append((backend_id, status, error_message))


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

    # check_status обновлён в БД независимо от Telegram (бот отключён).
    assert repo.updates == [(backend.id, BackendStatus.working.value, None)]


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

    # Домен из снимка передан в проверку доступности.
    assert captured["domain"] == "api.example.com"


async def test_check_one_error_first_check_updates_and_alerts_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend(status=BackendStatus.pending.value)
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("error", "Бэк недоступен")
    )

    await svc.check_one(backend.id)

    assert repo.updates == [(backend.id, BackendStatus.error.value, "Бэк недоступен")]
    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
    assert 'Бэк "API EU" [api-eu] api.example.com' in tg.sent[0]
    assert 'Бэк не работает: "Бэк недоступен"' in tg.sent[0]


async def test_check_one_recovery_sends_green_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(status=BackendStatus.error.value)
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("working", None))

    await svc.check_one(backend.id)

    assert repo.updates == [(backend.id, BackendStatus.working.value, None)]
    assert len(tg.sent) == 1
    assert "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢" in tg.sent[0]
    assert "Бэк снова работает" in tg.sent[0]


async def test_check_one_error_alert_suppressed_when_telegram_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeBackend(status=BackendStatus.working.value)
    repo = _FakeRepo(backend)
    svc = _make_service(
        monkeypatch,
        repo,
        telegram=None,
        outcome=BackendCheckResult("error", "Таймаут подключения"),
    )

    await svc.check_one(backend.id)

    # Статус пишется, но отправлять некуда (бот отключён) — исключений нет.
    assert repo.updates == [(backend.id, BackendStatus.error.value, "Таймаут подключения")]


async def test_check_one_working_to_working_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _FakeBackend(status=BackendStatus.working.value)
    repo = _FakeRepo(backend)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=BackendCheckResult("working", None))

    await svc.check_one(backend.id)

    assert repo.updates == [(backend.id, BackendStatus.working.value, None)]
    assert tg.sent == []  # working→working молча


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

    # Исключение внутри check_one логируется и НЕ всплывает наружу.
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

    assert repo.updates == [(backend.id, BackendStatus.error.value, "Ошибка бэка (HTTP 500)")]
    assert len(tg.sent) == 1


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

    assert state["n"] == 2  # пережил ошибку первой итерации, выполнил вторую
