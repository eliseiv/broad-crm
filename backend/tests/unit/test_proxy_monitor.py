"""Unit-тесты монитора прокси check_one/poll_once/_check_snapshot/run (ADR-019/027).

Репозиторий, сессия БД, расшифровка и проверка доступности — стабы (без сети/БД).
Покрывают: grace-порог алерта (🔴 только после непрерывной недоступности ≥ порога,
ADR-027), recovery только если 🔴 был, обновление check_status при конклюзивном исходе
независимо от Telegram, гейт Telegram (`telegram=None` → не отправляем, статус пишем),
расшифровку пароля перед проверкой, сбой расшифровки → без обновления, overall-deadline
(`asyncio.wait_for` прерывает зависшую проверку → «Таймаут подключения»), устойчивость
check_one/run к исключениям. `_check_snapshot` персистит error_since/alert_sent через
update_check. Исхода `unknown` у прокси НЕТ.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.infra.proxy_check import REASON_TIMEOUT, ProxyCheckResult
from app.models.proxy import ProxyStatus
from app.services import proxy_monitor_service as mod
from app.services.proxy_monitor_service import ProxyMonitorService


class _FakeProxy:
    def __init__(
        self,
        *,
        status: str = ProxyStatus.pending.value,
        password_encrypted: bytes | None = b"cipher",
        error_since: datetime | None = None,
        alert_sent: bool = False,
    ) -> None:
        self.id = uuid.uuid4()
        self.name = "DE Residential"
        self.proxy_type = "socks5"
        self.host = "proxy.example.com"
        self.port = 1080
        self.username = "user01"
        self.password_encrypted = password_encrypted
        self.check_status = status
        self.error_since = error_since
        self.alert_sent = alert_sent


class _FakeRepo:
    def __init__(self, proxy: _FakeProxy | None) -> None:
        self.proxy = proxy
        self.updates: list[dict[str, Any]] = []

    async def get_by_id(self, proxy_id: uuid.UUID) -> _FakeProxy | None:
        if self.proxy is not None and proxy_id == self.proxy.id:
            return self.proxy
        return None

    async def list_all(self) -> list[_FakeProxy]:
        return [self.proxy] if self.proxy is not None else []

    async def update_check(
        self,
        proxy_id: uuid.UUID,
        *,
        status: str,
        error_message: str | None,
        last_checked_at: object,
        error_since: datetime | None,
        alert_sent: bool,
    ) -> None:
        self.updates.append(
            {
                "id": proxy_id,
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
    outcome: ProxyCheckResult,
    captured: dict[str, object] | None = None,
) -> ProxyMonitorService:
    monkeypatch.setattr(mod, "ProxyRepository", lambda _session: repo)
    monkeypatch.setattr(mod, "decrypt_secret", lambda _enc: "decrypted-pass")

    async def _fake_check_proxy(
        proxy_type: str,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
    ) -> ProxyCheckResult:
        if captured is not None:
            captured["password"] = password
            captured["username"] = username
            captured["proxy_type"] = proxy_type
        return outcome

    monkeypatch.setattr(mod, "check_proxy", _fake_check_proxy)

    return ProxyMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- check_one
async def test_check_one_working_updates_db_no_telegram_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _FakeProxy(status=ProxyStatus.pending.value)
    repo = _FakeRepo(proxy)
    svc = _make_service(monkeypatch, repo, telegram=None, outcome=ProxyCheckResult("working", None))

    await svc.check_one(proxy.id)

    # check_status обновлён в БД независимо от Telegram (бот отключён).
    assert repo.updates[0]["status"] == ProxyStatus.working.value
    assert repo.updates[0]["error_message"] is None


async def test_check_one_decrypts_password_before_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _FakeProxy(status=ProxyStatus.working.value, password_encrypted=b"cipher")
    repo = _FakeRepo(proxy)
    captured: dict[str, object] = {}
    svc = _make_service(
        monkeypatch,
        repo,
        telegram=None,
        outcome=ProxyCheckResult("working", None),
        captured=captured,
    )

    await svc.check_one(proxy.id)

    # Пароль расшифрован (decrypt_secret) и передан в проверку доступности.
    assert captured["password"] == "decrypted-pass"
    assert captured["username"] == "user01"


async def test_check_one_no_password_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = _FakeProxy(status=ProxyStatus.working.value, password_encrypted=None)
    repo = _FakeRepo(proxy)
    captured: dict[str, object] = {}
    svc = _make_service(
        monkeypatch,
        repo,
        telegram=None,
        outcome=ProxyCheckResult("working", None),
        captured=captured,
    )

    await svc.check_one(proxy.id)

    assert captured["password"] is None


async def test_check_one_decrypt_failure_skips_update(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.infra.crypto import CryptoError

    proxy = _FakeProxy(status=ProxyStatus.working.value, password_encrypted=b"broken")
    repo = _FakeRepo(proxy)

    def _boom(_enc: bytes) -> str:
        raise CryptoError("bad token")

    monkeypatch.setattr(mod, "ProxyRepository", lambda _session: repo)
    monkeypatch.setattr(mod, "decrypt_secret", _boom)

    svc = ProxyMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=None,
        settings=_settings(),  # type: ignore[arg-type]
    )

    await svc.check_one(proxy.id)

    # Расшифровка провалилась → БД не обновляется (проверка не выполнена).
    assert repo.updates == []


# ------------------------------------------------------- grace-порог алерта (ADR-027)
async def test_check_one_first_error_starts_grace_no_red(monkeypatch: pytest.MonkeyPatch) -> None:
    # Первый провал (working→error): статус error немедленно, но 🔴 НЕ шлём (grace, ADR-027).
    proxy = _FakeProxy(status=ProxyStatus.working.value)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("error", "Прокси недоступен")
    )

    await svc.check_one(proxy.id)

    assert repo.updates[0]["status"] == ProxyStatus.error.value
    assert repo.updates[0]["error_message"] == "Прокси недоступен"
    assert repo.updates[0]["error_since"] is not None  # начало эпизода зафиксировано
    assert repo.updates[0]["alert_sent"] is False
    assert tg.sent == []  # grace-окно не истекло → тихо


async def test_check_one_pending_first_error_starts_grace_no_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pending→error: тоже старт эпизода (error_since=now), 🔴 отложен.
    proxy = _FakeProxy(status=ProxyStatus.pending.value)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("error", "Прокси недоступен")
    )

    await svc.check_one(proxy.id)

    assert repo.updates[0]["status"] == ProxyStatus.error.value
    assert repo.updates[0]["error_since"] is not None
    assert repo.updates[0]["alert_sent"] is False
    assert tg.sent == []


async def test_check_one_error_within_grace_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    # Недоступен всего 10 мин (< 30): 🔴 ещё не шлём, error_since сохраняется.
    recent = datetime.now(UTC) - timedelta(minutes=10)
    proxy = _FakeProxy(status=ProxyStatus.error.value, error_since=recent, alert_sent=False)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("error", "Прокси недоступен")
    )

    await svc.check_one(proxy.id)

    assert repo.updates[0]["status"] == ProxyStatus.error.value
    assert repo.updates[0]["error_since"] == recent  # отсчёт не сбрасывается
    assert repo.updates[0]["alert_sent"] is False
    assert tg.sent == []


async def test_check_one_error_after_grace_sends_red(monkeypatch: pytest.MonkeyPatch) -> None:
    # Недоступен непрерывно > 30 мин (error_since в прошлом), 🔴 ещё не слали → отправить 🔴.
    past = datetime.now(UTC) - timedelta(minutes=31)
    proxy = _FakeProxy(status=ProxyStatus.error.value, error_since=past, alert_sent=False)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("error", "Прокси недоступен")
    )

    await svc.check_one(proxy.id)

    assert repo.updates[0]["alert_sent"] is True
    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
    assert 'Прокси "DE Residential" proxy.example.com:1080' in tg.sent[0]
    assert 'Прокси не работает: "Прокси недоступен"' in tg.sent[0]


async def test_check_one_error_after_grace_but_already_alerted_is_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Порог истёк, но 🔴 уже отправлен ранее → повторно не шлём (защита от дубля).
    past = datetime.now(UTC) - timedelta(minutes=45)
    proxy = _FakeProxy(status=ProxyStatus.error.value, error_since=past, alert_sent=True)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("error", "Прокси недоступен")
    )

    await svc.check_one(proxy.id)

    assert repo.updates[0]["alert_sent"] is True
    assert tg.sent == []


async def test_check_one_recovery_sends_green_when_alert_was_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = _FakeProxy(
        status=ProxyStatus.error.value,
        error_since=datetime.now(UTC) - timedelta(minutes=40),
        alert_sent=True,
    )
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("working", None))

    await svc.check_one(proxy.id)

    assert repo.updates[0]["status"] == ProxyStatus.working.value
    assert repo.updates[0]["error_since"] is None  # эпизод закрыт
    assert repo.updates[0]["alert_sent"] is False
    assert len(tg.sent) == 1
    assert "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢" in tg.sent[0]
    assert "Прокси снова работает" in tg.sent[0]


async def test_check_one_recovery_silent_when_alert_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Флап < 30 мин: 🔴 не слали → 🟢 не нужен (тихо), статус всё равно working.
    proxy = _FakeProxy(
        status=ProxyStatus.error.value,
        error_since=datetime.now(UTC) - timedelta(minutes=5),
        alert_sent=False,
    )
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("working", None))

    await svc.check_one(proxy.id)

    assert repo.updates[0]["status"] == ProxyStatus.working.value
    assert repo.updates[0]["error_since"] is None
    assert repo.updates[0]["alert_sent"] is False
    assert tg.sent == []


async def test_check_one_red_suppressed_when_telegram_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    past = datetime.now(UTC) - timedelta(minutes=31)
    proxy = _FakeProxy(status=ProxyStatus.error.value, error_since=past, alert_sent=False)
    repo = _FakeRepo(proxy)
    svc = _make_service(
        monkeypatch, repo, telegram=None, outcome=ProxyCheckResult("error", "Таймаут подключения")
    )

    await svc.check_one(proxy.id)

    # Статус/alert_sent пишутся, но отправлять некуда (бот отключён) — исключений нет.
    assert repo.updates[0]["status"] == ProxyStatus.error.value
    assert repo.updates[0]["alert_sent"] is True


async def test_check_one_working_to_working_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = _FakeProxy(status=ProxyStatus.working.value)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("working", None))

    await svc.check_one(proxy.id)

    assert repo.updates[0]["status"] == ProxyStatus.working.value
    assert tg.sent == []  # working→working молча


async def test_check_one_missing_proxy_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeRepo(None)
    svc = _make_service(monkeypatch, repo, telegram=None, outcome=ProxyCheckResult("working", None))

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

    svc = ProxyMonitorService(
        sessionmaker=lambda: _BoomSession(),  # type: ignore[arg-type]
        telegram=None,
        settings=_settings(),  # type: ignore[arg-type]
    )

    # Исключение внутри check_one логируется и НЕ всплывает наружу.
    await svc.check_one(uuid.uuid4())

    assert "proxy_check_one_failed" in events


# ---------------------------------------------------------------- poll_once
async def test_poll_once_empty_registry_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeRepo(None)
    svc = _make_service(monkeypatch, repo, telegram=None, outcome=ProxyCheckResult("working", None))

    await svc.poll_once()

    assert repo.updates == []


async def test_poll_once_checks_all_proxies_and_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = _FakeProxy(status=ProxyStatus.working.value)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=ProxyCheckResult("error", "Ошибка прокси")
    )

    await svc.poll_once()

    assert repo.updates[0]["status"] == ProxyStatus.error.value
    assert repo.updates[0]["error_message"] == "Ошибка прокси"
    # Первый провал → grace, 🔴 не шлём.
    assert tg.sent == []


# ---------------------------------------------------------------- run()
async def test_run_handles_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = ProxyMonitorService(
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
    svc = ProxyMonitorService(
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


# ------------------------------------------------- overall-deadline (ADR-024)
async def test_check_one_deadline_aborts_hung_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # Проверка прокси «зависает» (socks5-handshake) → overall-deadline (asyncio.wait_for)
    # прерывает её → конклюзивный error «Таймаут подключения». Первый провал (working→error)
    # → grace (ADR-027): 🔴 откладывается, статус error пишется сразу.
    monkeypatch.setenv("PROXY_CHECK_DEADLINE_SEC", "0.05")
    from app.config import get_settings

    get_settings.cache_clear()

    proxy = _FakeProxy(status=ProxyStatus.working.value)
    repo = _FakeRepo(proxy)
    tg = _FakeTelegram()
    monkeypatch.setattr(mod, "ProxyRepository", lambda _session: repo)
    monkeypatch.setattr(mod, "decrypt_secret", lambda _enc: "decrypted-pass")

    async def _hang(*_args: object, **_kw: object) -> ProxyCheckResult:
        await asyncio.Event().wait()  # никогда не завершится
        raise AssertionError("unreachable")

    monkeypatch.setattr(mod, "check_proxy", _hang)

    svc = ProxyMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=tg,  # type: ignore[arg-type]
        settings=get_settings(),  # type: ignore[arg-type]
    )

    await svc.check_one(proxy.id)

    assert repo.updates[0]["status"] == ProxyStatus.error.value
    assert repo.updates[0]["error_message"] == REASON_TIMEOUT
    assert repo.updates[0]["error_since"] is not None  # эпизод стартовал
    assert tg.sent == []  # grace: первый провал не алертит немедленно
