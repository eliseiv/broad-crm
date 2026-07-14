"""Integration (ADR-053, TD-059): бюджеты вызова и ПРИЧИНА отказа доходят до ответа CRM.

Регресс прод-бага: агрегатор отвечал осмысленным `422` через ~31 с, CRM обрывала запрос
10-секундным таймаутом и показывала ложное `502 mail_unavailable` («сервис недоступен») —
пользователь не узнавал настоящую причину.

Реальный Postgres + FastAPI-app; агрегатор замокан на уровне httpx-ТРАНСПОРТА
(`FakeAggregatorTransport`), а не подменой клиента: только так проверяются фактические
бюджеты (`httpx.Timeout` по фазам + `asyncio.wait_for`) и число исходящих запросов.

⚠️ Точка инъекции суб-секундных бюджетов — КОНСТРУКТОР `MailClient` + override
`get_mail_service` (06-testing-strategy.md §Интеграционные). Через `Settings` их подменить
нельзя (`int` + `ge/le`), а override фабрик `get_mail_client`/`get_mail_server_client` был бы
no-op (сервис зовёт их прямым вызовом, а не через `Depends`).

Контракт проверяется по полю `error.code` ФАКТИЧЕСКОГО ответа, а не по тексту `message`.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from app.infra.mail_client import MailClient
from mail_s34_helpers import (
    build_app,
    build_principal,
    client,
    dt,
    mail_db,
    seed_account,
    seed_message,
    seed_team,
)
from mail_transport_helpers import FakeAggregatorTransport, error_body, install_transport

_BASE = "https://postapp.example"

# Суб-секундные бюджеты категорий (прод: 10/30 и 75/85 — ADR-053 §1.1/§1.2).
# ВАЖНО: быстрый read (0.05) моделирует «прежний слишком короткий бюджет» из прод-бага,
# mail-server read (0.4) — долгий: ответ агрегатора с задержкой 0.15 обязан дойти.
_FAST_READ = 0.05
_FAST_DEADLINE = 0.6
_SLOW_READ = 0.4
_SLOW_DEADLINE = 1.0

# Задержка «осмысленного» ответа агрегатора: больше быстрого бюджета, меньше mail-server.
_AGGREGATOR_DELAY = 0.15

_CREDS = {
    "email": "new@example.com",
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_ssl": True,
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_ssl": True,
    "smtp_starttls": False,
    "password": "s3cr3t-imap-pass",
}


class LogSpy:
    """Спай structlog-логгера сервиса.

    `structlog.testing.capture_logs()` здесь НЕ годится: `configure_logging` включает
    `cache_logger_on_first_use=True` (`app/logging.py:64`), а `mail_service.logger` —
    модульный прокси, закешировавший bound-логгер на первом использовании (возможно, в
    другом тесте процесса). Подмена самого `logger` даёт детерминированный перехват без
    зависимости от глобального состояния structlog.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _record(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    warning = _record
    info = _record
    error = _record
    debug = _record

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def payload(self, event: str) -> dict[str, Any]:
        return next(kwargs for name, kwargs in self.events if name == event)


def _spy_service_logger(monkeypatch: pytest.MonkeyPatch) -> LogSpy:
    from app.services import mail_service

    spy = LogSpy()
    monkeypatch.setattr(mail_service, "logger", spy)
    return spy


class RecordingMailClient(MailClient):
    """Клиент со спаем на `delete_mailbox`: фиксирует пер-вызовный `deadline_sec` (§1.2.2)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.delete_calls: list[tuple[int, float | None]] = []

    async def delete_mailbox(
        self, mailbox_id: int, *, deadline_sec: float | None = None
    ) -> dict[str, Any]:
        self.delete_calls.append((mailbox_id, deadline_sec))
        return await super().delete_mailbox(mailbox_id, deadline_sec=deadline_sec)


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


def _fast_client(*, recording: bool = False) -> MailClient:
    """Клиент БЫСТРОЙ категории (`delete`/`sync`/`oauth-authorize`)."""
    cls = RecordingMailClient if recording else MailClient
    return cls(
        base_url=_BASE,
        api_key="test-key",
        read_timeout_sec=_FAST_READ,
        deadline_sec=_FAST_DEADLINE,
    )


def _slow_client() -> MailClient:
    """Клиент MAIL-SERVER категории (`test`/`create`/`patch`/`reply`)."""
    return MailClient(
        base_url=_BASE,
        api_key="test-key",
        read_timeout_sec=_SLOW_READ,
        deadline_sec=_SLOW_DEADLINE,
    )


def _app(sm: Any, *, fast: MailClient, slow: MailClient) -> Any:
    return build_app(
        sm,
        build_principal(is_superadmin=True),
        mail_client=fast,
        mail_server_client=slow,
    )


async def _seed_team(sm: Any) -> str:
    async with sm() as s:
        team = await seed_team(s)
        await s.commit()
        return str(team.id)


async def _seed_account(sm: Any, account_id: int) -> None:
    async with sm() as s:
        team = await seed_team(s)
        await seed_account(s, account_id=account_id, team_id=team.id)
        await s.commit()


async def _seed_message(sm: Any) -> None:
    async with sm() as s:
        team = await seed_team(s)
        await seed_account(s, account_id=1, team_id=team.id)
        await seed_message(
            s,
            account_id=1,
            uid=1,
            internal_date=dt(),
            from_addr="orig@example.com",
            message_id_header="<orig@example.com>",
        )
        await s.commit()


# =============================================================================
# §2 — 422 агрегатора: истинная причина отказа доходит до пользователя
# =============================================================================


async def test_test_mailbox_slow_422_imap_returns_mail_imap_failed_not_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ПРЯМОЙ РЕГРЕСС прод-бага: 422 приходит ПОЗЖЕ прежнего (быстрого) бюджета.

    Задержка 0.15 с > read быстрого клиента (0.05) — со старым единым бюджетом запрос был бы
    оборван и пользователь получил бы `502 mail_unavailable`. Mail-server-бюджет (0.4) больше
    задержки → осмысленный `422` агрегатора доходит и маппится в `422 mail_imap_failed`.
    """
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "POST",
        "/mailboxes/test",
        status=422,
        json_body=error_body("imap_login_failed"),
        delay_sec=_AGGREGATOR_DELAY,
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/test", json=_CREDS)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "mail_imap_failed"
    assert len(transport.calls) == 1  # ретрая write нет


async def test_test_mailbox_422_smtp_returns_mail_smtp_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "POST",
        "/mailboxes/test",
        status=422,
        json_body=error_body("smtp_login_failed"),
        delay_sec=_AGGREGATOR_DELAY,
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/test", json=_CREDS)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "mail_smtp_failed"


async def test_test_mailbox_422_invalid_host_returns_mail_invalid_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "POST", "/mailboxes/test", status=422, json_body=error_body("invalid_host")
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/test", json=_CREDS)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "mail_invalid_host"


async def test_test_mailbox_422_unknown_code_falls_back_to_unprocessable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "POST", "/mailboxes/test", status=422, json_body=error_body("something_else")
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/test", json=_CREDS)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unprocessable"  # прежнее поведение (fallback)


async def test_create_mailbox_422_imap_returns_mail_imap_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "POST",
        "/api/external/mailboxes",
        status=422,
        json_body=error_body("imap_login_failed"),
        delay_sec=_AGGREGATOR_DELAY,
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        team_id = await _seed_team(sm)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "mail_imap_failed"


async def test_patch_mailbox_422_smtp_returns_mail_smtp_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "PATCH",
        "/mailboxes/10",
        status=422,
        json_body=error_body("smtp_login_failed"),
        delay_sec=_AGGREGATOR_DELAY,
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_account(sm, 10)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.patch("/api/mail/mailboxes/10", json={"password": "new-pass"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "mail_smtp_failed"


# =============================================================================
# §2/§3 — таймауты: источник различим, `504 mail_timeout` ≠ «сервис недоступен»
# =============================================================================


async def test_test_mailbox_own_timeout_returns_504_mail_timeout_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Агрегатор молчит дольше read-бюджета mail-server-клиента → 504 mail_timeout, 1 запрос."""
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("POST", "/mailboxes/test", delay_sec=3.0)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/test", json=_CREDS)

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "mail_timeout"
    assert len(transport.calls) == 1  # анти-двойная-запись: таймаут НЕ ретраится


async def test_create_overall_deadline_cuts_retries_and_returns_504(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overall-deadline режет вызов вместе со ВСЕМИ ретраями и backoff (§1.2).

    Транспорт всегда даёт `ConnectError` (штатно ретраится, 3 попытки + backoff 0.7 с), но
    overall-deadline mail-server-клиента (0.35 с) истекает раньше → `504 mail_timeout`,
    а НЕ `502` после исчерпания ретраев. Ответ отдан ЗАДОЛГО до истечения read-фазы (5 с).
    """
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("POST", "/api/external/mailboxes", connect_errors=99)
    install_transport(monkeypatch, transport)

    slow = MailClient(base_url=_BASE, api_key="test-key", read_timeout_sec=5.0, deadline_sec=0.35)

    async with mail_db() as sm:
        team_id = await _seed_team(sm)
        app = _app(sm, fast=_fast_client(), slow=slow)
        async with client(app) as c:
            started = time.monotonic()
            resp = await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})
            elapsed = time.monotonic() - started

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "mail_timeout"
    assert elapsed < 2.0  # граница ЗАПРОСА держится, read-фаза (5 с) не дожидается
    assert len(transport.calls) < 3  # цикл ретраев обрублен дедлайном


async def test_aggregator_504_on_mailserver_path_returns_504_mail_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("POST", "/mailboxes/test", status=504)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/test", json=_CREDS)

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "mail_timeout"


async def test_aggregator_504_on_fast_path_returns_504_mail_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§2.1: `504` ОТ агрегатора → `504 mail_timeout` на ЛЮБОЙ категории, в т.ч. быстрой."""
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("DELETE", "/mailboxes/11", status=504)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_account(sm, 11)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.delete("/api/mail/mailboxes/11")

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "mail_timeout"


async def test_own_timeout_on_fast_path_returns_502_mail_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§2.1: СОБСТВЕННЫЙ таймаут CRM на быстром пути → `502 mail_unavailable`, НЕ `504`.

    Различение — по `MailTimeout.status_code` (None), а не по типу исключения: 30 с на чтение
    из БД/Redis агрегатора не хватило = он реально не в порядке.
    """
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("DELETE", "/mailboxes/12", delay_sec=3.0)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_account(sm, 12)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.delete("/api/mail/mailboxes/12")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"
    assert len(transport.calls) == 1  # таймаут не ретраится и на быстром пути


async def test_own_timeout_on_fast_sync_path_returns_502(monkeypatch: pytest.MonkeyPatch) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("POST", "/mailboxes/13/sync", delay_sec=3.0)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_account(sm, 13)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/13/sync")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


# =============================================================================
# §2 — прочие статусы агрегатора
# =============================================================================


@pytest.mark.parametrize("status_code", [503, 429])
async def test_aggregator_5xx_and_429_map_to_mail_unavailable(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("POST", "/mailboxes/14/sync", status=status_code)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_account(sm, 14)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/14/sync")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


async def test_connect_error_retried_three_times_then_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ConnectError` РЕТРАИТСЯ (запрос заведомо не ушёл) → 3 попытки → `502 mail_unavailable`."""
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("DELETE", "/mailboxes/15", connect_errors=99)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_account(sm, 15)
        # Deadline щедрый — чтобы ретраи успели отработать полностью (проверяем именно их).
        fast = MailClient(
            base_url=_BASE, api_key="test-key", read_timeout_sec=0.5, deadline_sec=10.0
        )
        app = _app(sm, fast=fast, slow=_slow_client())
        async with client(app) as c:
            resp = await c.delete("/api/mail/mailboxes/15")

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"
    assert len(transport.calls) == 3


async def test_unexpected_aggregator_status_401_is_catch_all_mail_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch-all прочих не-2xx (протухший `MAIL_API_KEY` → 401) → `502` + лог (§2)."""
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("POST", "/mailboxes/test", status=401)
    install_transport(monkeypatch, transport)
    spy = _spy_service_logger(monkeypatch)

    async with mail_db() as sm:
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/mailboxes/test", json=_CREDS)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"
    assert "mail_write_unexpected_aggregator_status" in spy.names()
    assert spy.payload("mail_write_unexpected_aggregator_status")["status"] == 401


# =============================================================================
# §2 — reply: `502 smtp_failed` агрегатора ≠ «сервис недоступен»
# =============================================================================


async def test_reply_aggregator_502_smtp_failed_returns_mail_send_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Транспорт донёс `status_code`+`error_code` из 5xx-ветки → `502 mail_send_failed`."""
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "POST", "/send", status=502, json_body=error_body("smtp_failed")
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "Спасибо!"})

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_send_failed"  # НЕ mail_unavailable


async def test_reply_own_timeout_returns_504_mail_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on("POST", "/send", delay_sec=3.0)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "Спасибо!"})

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "mail_timeout"
    assert len(transport.calls) == 1  # отправка НЕ повторяется (анти-двойная-отправка)


async def test_reply_slow_502_smtp_failed_arrives_within_mailserver_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Медленная, но состоявшаяся отправка не обрывается CRM'ом (задержка > быстрого бюджета)."""
    await _enable_mail(monkeypatch)
    transport = FakeAggregatorTransport().on(
        "POST",
        "/send",
        json_body={"sent_id": 1, "smtp_message_id": "<m@x>"},
        delay_sec=_AGGREGATOR_DELAY,
    )
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            resp = await c.post("/api/mail/messages/1/reply", json={"body": "Спасибо!"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["smtp_message_id"] == "<m@x>"


# =============================================================================
# §1.2.2 — компенсирующая уборка сироты
# =============================================================================


async def _break_catalog_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.repositories.mail_account_repository import MailAccountRepository

    async def _boom_create(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("catalog insert failed")

    monkeypatch.setattr(MailAccountRepository, "create", _boom_create)


async def test_orphan_cleanup_uses_short_deadline_from_config_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Уборка идёт под КОРОТКИМ пер-вызовным deadline = `config.MAIL_CLEANUP_DEADLINE_SEC`.

    Сверяется с ИМПОРТИРОВАННОЙ константой (литерал `15` в `mail_service.py` запрещён,
    §1.3 п.7б), и она строго меньше полного бюджета быстрой категории — уборка не вправе
    съедать его целиком (иначе бюджет ЗАПРОСА `create` выходит за nginx, §1.2.1).
    """
    from app.config import MAIL_CLEANUP_DEADLINE_SEC, get_settings

    await _enable_mail(monkeypatch)
    await _break_catalog_insert(monkeypatch)

    transport = FakeAggregatorTransport()
    transport.on("POST", "/api/external/mailboxes", status=201, json_body={"id": 777})
    transport.on("DELETE", "/mailboxes/777", status=204)
    install_transport(monkeypatch, transport)

    fast = _fast_client(recording=True)
    assert isinstance(fast, RecordingMailClient)

    async with mail_db() as sm:
        team_id = await _seed_team(sm)
        app = _app(sm, fast=fast, slow=_slow_client())
        async with client(app) as c:
            # Исходная ошибка вставки каталога проброшена (не подменена ошибкой уборки).
            with pytest.raises(RuntimeError, match="catalog insert failed"):
                await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})

    # Второй сетевой вызов в ПРЕДЕЛАХ ОДНОГО HTTP-запроса — компенсирующее удаление сироты.
    assert [m for m, _ in transport.calls] == ["POST", "DELETE"]
    assert fast.delete_calls == [(777, MAIL_CLEANUP_DEADLINE_SEC)]
    assert get_settings().mail_api_deadline_sec > MAIL_CLEANUP_DEADLINE_SEC


async def test_orphan_cleanup_timeout_does_not_mask_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Таймаут компенсирующего `DELETE` НЕ пролетает наружу (§1.2.2: `MailTimeout` в catch-листе).

    Наружу уходит ИСХОДНАЯ ошибка вставки каталога; провал уборки только логируется.
    """
    await _enable_mail(monkeypatch)
    await _break_catalog_insert(monkeypatch)

    transport = FakeAggregatorTransport()
    transport.on("POST", "/api/external/mailboxes", status=201, json_body={"id": 778})
    transport.on("DELETE", "/mailboxes/778", delay_sec=3.0)  # уборка молчит → MailTimeout
    install_transport(monkeypatch, transport)
    spy = _spy_service_logger(monkeypatch)

    async with mail_db() as sm:
        team_id = await _seed_team(sm)
        app = _app(sm, fast=_fast_client(), slow=_slow_client())
        async with client(app) as c:
            with pytest.raises(RuntimeError, match="catalog insert failed"):
                await c.post("/api/mail/mailboxes", json={**_CREDS, "team_id": team_id})

    assert [m for m, _ in transport.calls] == ["POST", "DELETE"]
    # Провал уборки только логируется — исходная ошибка (RuntimeError) дошла до клиента.
    assert spy.payload("mail_create_orphan_cleanup_failed")["error_type"] == "MailTimeout"
