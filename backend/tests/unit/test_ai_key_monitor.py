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


class _FakeBackend:
    """Строка `backends` для перечня бэков в алерте «Ключ не работает» (ADR-046 §1)."""

    def __init__(self, *, position: int, code: str, name: str, domain: str) -> None:
        self.position = position
        self.code = code
        self.name = name
        self.domain = domain


class _FakeBackendRepo:
    """Стаб BackendRepository: `_send_alert` резолвит бэки ключа на фейковой сессии.

    По умолчанию бэков нет → блок «Бэки:» не добавляется, текст побайтово равен прежнему.
    `calls` фиксирует, для каких ключей перечень вообще запрашивался (при выключенном боте
    и при recovery-алерте лишний SELECT не делается).
    """

    by_key: dict[uuid.UUID, list[_FakeBackend]] = {}  # noqa: RUF012
    calls: list[uuid.UUID] = []  # noqa: RUF012

    def __init__(self, session: object) -> None:
        self._session = session

    async def list_by_ai_key(self, ai_key_id: uuid.UUID) -> list[_FakeBackend]:
        _FakeBackendRepo.calls.append(ai_key_id)
        return list(_FakeBackendRepo.by_key.get(ai_key_id, []))


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
    # `_send_alert` резолвит бэки ключа через BackendRepository на сессии сервиса (ADR-046 §1).
    monkeypatch.setattr(mod, "BackendRepository", _FakeBackendRepo)
    _FakeBackendRepo.by_key = {}
    _FakeBackendRepo.calls = []

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


# --------------------------------------------------------------------------------------
# Wiring блока «Бэки:» в РЕАЛЬНОМ пути доставки алерта ключа (ADR-046 §1): `_send_alert`
# резолвит бэки, использующие ключ (`backends.ai_key_id`), через `BackendRepository` на
# сессии сервиса и подаёт их в `build_key_error`. Формат строки бэка покрыт побайтово
# отдельно (test_notifications_backends_block.py) — здесь проверяется, что перечень реально
# доезжает до ОТПРАВЛЕННОГО сообщения и что при recovery/выключенном боте SELECT'а нет.
# --------------------------------------------------------------------------------------
def _seed_key_backends(ai_key_id: uuid.UUID) -> None:
    """Два бэка ключа, поданных в ОБРАТНОМ требуемом порядке (`position ASC, code ASC`)."""
    _FakeBackendRepo.by_key[ai_key_id] = [
        _FakeBackend(position=1, code="web", name="Web", domain="https://web.example.com"),
        _FakeBackend(position=0, code="api-eu", name="API EU", domain="https://eu.example.com"),
    ]


async def test_key_error_alert_carries_backends_block_byte_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _FakeAiKey(status=AiKeyStatus.pending.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("error", "Недостаточно средств")
    )
    _seed_key_backends(key.id)

    await svc.check_one(key.id)

    assert len(tg.sent) == 1
    # Побайтово — по modules/ai-keys «Формат сообщений Telegram» + блок «Бэки:» (ADR-046 §1);
    # порядок перечня `position ASC, code ASC` (подан обратный → сортировка in-memory).
    assert tg.sent[0] == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Ключ "OpenAI Prod" ****bA3T\n'
        'Ключ не работает: "Недостаточно средств"\n'
        "\n"
        "Бэки:\n"
        'Бэк "API EU" [api-eu] https://eu.example.com\n'
        'Бэк "Web" [web] https://web.example.com'
    )
    assert _FakeBackendRepo.calls == [key.id]


async def test_key_error_alert_without_backends_is_byte_equal_to_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бэков у ключа нет → блок не добавляется: сообщение побайтово равно прежнему."""
    key = _FakeAiKey(status=AiKeyStatus.pending.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(
        monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("error", "Недостаточно средств")
    )

    await svc.check_one(key.id)

    assert _FakeBackendRepo.calls == [key.id]  # спросили…
    assert tg.sent[0] == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Ключ "OpenAI Prod" ****bA3T\n'
        'Ключ не работает: "Недостаточно средств"'
    )  # …но перечень пуст → блока нет


async def test_key_recovery_alert_does_not_resolve_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery перечнем НЕ расширяется (ADR-046 §1) → SELECT бэков не делается вовсе."""
    key = _FakeAiKey(status=AiKeyStatus.error.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("working", None))
    _seed_key_backends(key.id)  # бэки ЕСТЬ, но их не должны запрашивать

    await svc.check_one(key.id)

    assert _FakeBackendRepo.calls == []
    assert "Бэки:" not in tg.sent[0]
    assert "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢" in tg.sent[0]


async def test_backends_not_resolved_when_telegram_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бот выключен → сообщение не формируется, лишний SELECT бэков не делается (§1)."""
    key = _FakeAiKey(status=AiKeyStatus.pending.value)
    repo = _FakeRepo(key)
    svc = _make_service(
        monkeypatch, repo, telegram=None, outcome=KeyCheckResult("error", "Недостаточно средств")
    )
    _seed_key_backends(key.id)

    await svc.check_one(key.id)

    assert repo.updates == [(key.id, AiKeyStatus.error.value, "Недостаточно средств")]
    assert _FakeBackendRepo.calls == []


async def test_poll_once_error_alert_carries_backends_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тот же wiring на цикле опроса (poll_once), а не только на ad-hoc check_one."""
    key = _FakeAiKey(status=AiKeyStatus.working.value)
    repo = _FakeRepo(key)
    tg = _FakeTelegram()
    svc = _make_service(monkeypatch, repo, telegram=tg, outcome=KeyCheckResult("error", "401"))
    _seed_key_backends(key.id)

    await svc.poll_once()

    assert _FakeBackendRepo.calls == [key.id]
    assert tg.sent[0].endswith(
        "\n\nБэки:\n"
        'Бэк "API EU" [api-eu] https://eu.example.com\n'
        'Бэк "Web" [web] https://web.example.com'
    )
