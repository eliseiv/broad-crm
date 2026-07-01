"""Unit-тесты монитора AI-ключей check_one/poll_once/_check_snapshot/run (modules/ai-keys).

Репозиторий, сессия БД, расшифровка и проверка провайдера — стабы (без сети/БД).
Покрывают: обновление check_status при конклюзивном исходе независимо от Telegram;
`unknown` → строка НЕ трогается; гейт Telegram (`telegram=None` → не отправляем,
статус всё равно пишем); отправка алертов при переходах; устойчивость check_one к
исключению; устойчивость run к ошибке итерации и отмене.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from app.infra.ai_provider import KeyCheckResult
from app.models.ai_key import AiKeyStatus
from app.services import ai_key_monitor_service as mod
from app.services.ai_key_monitor_service import AiKeyMonitorService


class _FakeAiKey:
    def __init__(self, status: str = AiKeyStatus.pending.value) -> None:
        self.id = uuid.uuid4()
        self.name = "OpenAI Prod"
        self.provider = "openai"
        self.key_encrypted = b"cipher"
        self.check_status = status
        self.key_last4 = "bA3T"


class _FakeRepo:
    def __init__(self, key: _FakeAiKey | None) -> None:
        self.key = key
        self.updates: list[tuple[uuid.UUID, str, str | None]] = []

    async def get_by_id(self, ai_key_id: uuid.UUID) -> _FakeAiKey | None:
        if self.key is not None and ai_key_id == self.key.id:
            return self.key
        return None

    async def list_all(self) -> list[_FakeAiKey]:
        return [self.key] if self.key is not None else []

    async def update_check(
        self,
        ai_key_id: uuid.UUID,
        *,
        status: str,
        error_message: str | None,
        last_checked_at: object,
    ) -> None:
        self.updates.append((ai_key_id, status, error_message))


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
    outcome: KeyCheckResult,
) -> AiKeyMonitorService:
    monkeypatch.setattr(mod, "AiKeyRepository", lambda _session: repo)
    monkeypatch.setattr(mod, "decrypt_secret", lambda _enc: "sk-proj-secret-bA3T")

    async def _fake_check_key(_provider: object, _key: str) -> KeyCheckResult:
        return outcome

    monkeypatch.setattr(mod, "check_key", _fake_check_key)

    return AiKeyMonitorService(
        sessionmaker=lambda: _FakeSession(),  # type: ignore[arg-type]
        telegram=telegram,  # type: ignore[arg-type]
        settings=_settings(),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- check_one
async def test_check_one_working_updates_db_no_telegram_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _FakeAiKey(status=AiKeyStatus.pending.value)
    repo = _FakeRepo(key)
    svc = _make_service(monkeypatch, repo, telegram=None, outcome=KeyCheckResult("working", None))

    await svc.check_one(key.id)

    # check_status обновлён в БД независимо от Telegram (бот отключён).
    assert repo.updates == [(key.id, AiKeyStatus.working.value, None)]


async def test_check_one_error_first_check_updates_and_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _FakeAiKey(status=AiKeyStatus.pending.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("error", "Недостаточно средств")
    )

    await svc.check_one(key.id)

    assert repo.updates == [(key.id, AiKeyStatus.error.value, "Недостаточно средств")]
    assert len(tg.sent) == 1
    assert "🔴🔴🔴СРОЧНО🔴🔴🔴" in tg.sent[0]
    assert 'Ключ "OpenAI Prod" ****bA3T' in tg.sent[0]
    assert 'Ключ не работает: "Недостаточно средств"' in tg.sent[0]


async def test_check_one_recovery_sends_green_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _FakeAiKey(status=AiKeyStatus.error.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("working", None))

    await svc.check_one(key.id)

    assert repo.updates == [(key.id, AiKeyStatus.working.value, None)]
    assert len(tg.sent) == 1
    assert "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢" in tg.sent[0]
    assert "Ключ снова работает" in tg.sent[0]


async def test_check_one_error_alert_suppressed_when_telegram_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _FakeAiKey(status=AiKeyStatus.working.value)
    repo = _FakeRepo(key)
    svc = _make_service(
        monkeypatch, repo, telegram=None, outcome=KeyCheckResult("error", "Ключ недействителен")
    )

    await svc.check_one(key.id)

    # Статус пишется, но отправлять некуда (бот отключён) — исключений нет.
    assert repo.updates == [(key.id, AiKeyStatus.error.value, "Ключ недействителен")]


async def test_check_one_unknown_does_not_touch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _FakeAiKey(status=AiKeyStatus.working.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("unknown", None))

    await svc.check_one(key.id)

    assert repo.updates == []  # строка ai_keys не обновляется вообще
    assert tg.sent == []


async def test_check_one_missing_key_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeRepo(None)
    svc = _make_service(monkeypatch, repo, telegram=None, outcome=KeyCheckResult("working", None))

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

    svc = AiKeyMonitorService(
        sessionmaker=lambda: _BoomSession(),  # type: ignore[arg-type]
        telegram=None,
        settings=_settings(),  # type: ignore[arg-type]
    )

    # Исключение внутри check_one логируется и НЕ всплывает наружу.
    await svc.check_one(uuid.uuid4())

    assert "ai_key_check_one_failed" in events


# ---------------------------------------------------------------- poll_once
async def test_poll_once_empty_registry_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _FakeRepo(None)
    svc = _make_service(monkeypatch, repo, telegram=None, outcome=KeyCheckResult("working", None))

    await svc.poll_once()

    assert repo.updates == []


async def test_poll_once_checks_all_keys_and_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _FakeAiKey(status=AiKeyStatus.working.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("error", "Доступ запрещён")
    )

    await svc.poll_once()

    assert repo.updates == [(key.id, AiKeyStatus.error.value, "Доступ запрещён")]
    assert len(tg.sent) == 1


async def test_poll_once_unknown_skips_update(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _FakeAiKey(status=AiKeyStatus.working.value)
    repo = _FakeRepo(key)
    svc = _make_service(monkeypatch, repo, telegram=None, outcome=KeyCheckResult("unknown", None))

    await svc.poll_once()

    assert repo.updates == []


# ---------------------------------------------------------------- run()
async def test_run_handles_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = AiKeyMonitorService(
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
    svc = AiKeyMonitorService(
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
