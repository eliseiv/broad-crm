"""Unit-тесты бюджетов и различения источника таймаута в транспорте почты (ADR-053, TD-059).

Покрывает §1.2 (httpx.Timeout ПО ФАЗАМ + overall-deadline `asyncio.wait_for` вокруг ВСЕХ
попыток и backoff), §1.2.2 (пер-вызовный override overall-deadline), §1.3 п.1/п.2/п.3/п.4
(`MailTimeout` со `status_code`: `504` = ОТ агрегатора, `None` = СОБСТВЕННЫЙ таймаут CRM;
`error_code` во ВСЕХ не-2xx ветках), §1.3 п.5 (ретрай-политика ADR-038 §1 не сломана),
§1.3 п.6 (две фабрики клиентов по категориям путей).

Бюджеты — суб-секундные, инъектируются В КОНСТРУКТОР клиента (06-testing-strategy.md
§Интеграционные): `Settings` тут не участвует вовсе.
"""

from __future__ import annotations

import time

import pytest
from app.infra.mail_client import (
    MailClient,
    MailRejected,
    MailTimeout,
    MailUnavailable,
    get_mail_client,
    get_mail_server_client,
)
from mail_transport_helpers import FakeAggregatorTransport, error_body, install_transport

_BASE = "https://postapp.example"
_KEY = "secret-api-key-value"


def _client(
    monkeypatch: pytest.MonkeyPatch,
    transport: FakeAggregatorTransport,
    *,
    read_timeout_sec: float = 0.3,
    deadline_sec: float = 1.0,
) -> MailClient:
    install_transport(monkeypatch, transport)
    return MailClient(
        base_url=_BASE,
        api_key=_KEY,
        read_timeout_sec=read_timeout_sec,
        deadline_sec=deadline_sec,
    )


# --- §1.2: httpx.Timeout ПО ФАЗАМ, а не одиночным float ----------------------
async def test_timeout_is_per_phase_not_single_float(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeAggregatorTransport()
    client = _client(monkeypatch, transport, read_timeout_sec=0.4)

    await client.test_mailbox({"email": "a@b.c"})

    # connect/write/pool — фиксированные константы кода; read — бюджет КАТЕГОРИИ клиента.
    # Одиночный float дал бы одинаковые значения во всех фазах (исходный дефект).
    assert transport.timeouts[0] == {"connect": 5.0, "read": 0.4, "write": 10.0, "pool": 5.0}


async def test_read_phase_uses_client_budget_not_connect_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeAggregatorTransport()
    client = _client(monkeypatch, transport, read_timeout_sec=0.25)

    await client.send_message(7, {"body_text": "hi"})

    phases = transport.timeouts[0]
    assert phases["read"] == 0.25
    assert phases["connect"] == 5.0  # долгий бюджет НЕ протекает в connect (§1.2)


# --- §1.3 п.1/п.2: собственный таймаут CRM → MailTimeout(status_code=None) ----
async def test_read_timeout_raises_mail_timeout_without_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Агрегатор «молчит» дольше read-бюджета инъектированного клиента.
    transport = FakeAggregatorTransport().on("POST", "/mailboxes/test", delay_sec=1.0, status=200)
    client = _client(monkeypatch, transport, read_timeout_sec=0.2, deadline_sec=5.0)

    with pytest.raises(MailTimeout) as exc:
        await client.test_mailbox({"email": "a@b.c"})

    assert exc.value.status_code is None  # СОБСТВЕННЫЙ таймаут CRM
    assert len(transport.calls) == 1  # ретрая нет (анти-двойная-запись, ADR-038 §1)


async def test_overall_deadline_cuts_call_with_all_retries_and_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overall-deadline режет ВЕСЬ вызов: попытки ретрая + backoff-паузы (§1.2).

    Транспорт всегда даёт `ConnectError` → штатный ретрай (3 попытки, backoff 0.2 + 0.5 =
    0.7 с). Deadline 0.3 с истекает РАНЬШЕ → `MailTimeout(status_code=None)`, а не
    `MailUnavailable` после трёх попыток. Per-phase-лимиты такой границы не дают.
    """
    transport = FakeAggregatorTransport().on("POST", "/mailboxes", connect_errors=99)
    client = _client(monkeypatch, transport, read_timeout_sec=5.0, deadline_sec=0.3)

    started = time.monotonic()
    with pytest.raises(MailTimeout) as exc:
        await client.create_mailbox({"email": "a@b.c"})
    elapsed = time.monotonic() - started

    assert exc.value.status_code is None
    assert elapsed < 0.7  # раньше, чем отработали бы все ретраи с backoff
    assert len(transport.calls) < 3  # цикл ретраев обрублен дедлайном


async def test_overall_deadline_cuts_slow_response_before_read_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deadline меньше read-бюджета → срабатывает он (граница ЗАПРОСА, а не только фазы)."""
    transport = FakeAggregatorTransport().on("PATCH", "/mailboxes/5", delay_sec=3.0)
    client = _client(monkeypatch, transport, read_timeout_sec=5.0, deadline_sec=0.25)

    started = time.monotonic()
    with pytest.raises(MailTimeout) as exc:
        await client.update_mailbox(5, {"password": "x"})
    elapsed = time.monotonic() - started

    assert exc.value.status_code is None
    assert elapsed < 1.0  # обработчик вернулся задолго до истечения read-бюджета (5 с)


# --- §1.2.2: пер-вызовный override overall-deadline (компенсирующая уборка) ---
async def test_per_call_deadline_override_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeAggregatorTransport().on("DELETE", "/mailboxes/9", delay_sec=3.0)
    # Deadline клиента щедрый (5 с), но вызов идёт с коротким override (0.2 с).
    client = _client(monkeypatch, transport, read_timeout_sec=5.0, deadline_sec=5.0)

    started = time.monotonic()
    with pytest.raises(MailTimeout) as exc:
        await client.delete_mailbox(9, deadline_sec=0.2)
    elapsed = time.monotonic() - started

    assert exc.value.status_code is None
    assert elapsed < 1.0  # срезано коротким override, а не 5-секундным deadline клиента


async def test_without_override_client_deadline_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeAggregatorTransport().on("DELETE", "/mailboxes/9", delay_sec=3.0)
    client = _client(monkeypatch, transport, read_timeout_sec=5.0, deadline_sec=0.2)

    with pytest.raises(MailTimeout):
        await client.delete_mailbox(9)  # override не передан → deadline клиента

    assert len(transport.calls) == 1


# --- §1.3 п.1/п.2/п.4: `504` ОТ агрегатора → MailTimeout(status_code=504) -----
async def test_gateway_timeout_from_aggregator_carries_status_504(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeAggregatorTransport().on(
        "POST", "/mailboxes/test", status=504, json_body=error_body("gateway_timeout")
    )
    client = _client(monkeypatch, transport)

    with pytest.raises(MailTimeout) as exc:
        await client.test_mailbox({"email": "a@b.c"})

    # Источник таймаута различим: 504 = агрегатор ДОСТУПЕН и сам сообщил «не успел».
    assert exc.value.status_code == 504
    assert exc.value.error_code == "gateway_timeout"
    assert len(transport.calls) == 1  # 504 не ретраится


# --- §1.3 п.3: status_code/error_code во ВСЕХ не-2xx ветках -------------------
async def test_502_smtp_failed_keeps_status_and_error_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeAggregatorTransport().on(
        "POST", "/send", status=502, json_body=error_body("smtp_failed")
    )
    client = _client(monkeypatch, transport)

    with pytest.raises(MailUnavailable) as exc:
        await client.send_message(3, {"body_text": "hi"})

    # Без этих полей `502 smtp_failed` (удалённый SMTP отклонил письмо, агрегатор РАБОТАЛ)
    # неотличим от падения агрегатора — Дефект 2 ADR-053.
    assert exc.value.status_code == 502
    assert exc.value.error_code == "smtp_failed"


async def test_422_carries_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeAggregatorTransport().on(
        "POST", "/mailboxes/test", status=422, json_body=error_body("imap_login_failed")
    )
    client = _client(monkeypatch, transport)

    with pytest.raises(MailRejected) as exc:
        await client.test_mailbox({"email": "a@b.c"})

    assert exc.value.status_code == 422
    assert exc.value.error_code == "imap_login_failed"


async def test_429_maps_to_unavailable_with_status(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeAggregatorTransport().on("POST", "/sync", status=429)
    client = _client(monkeypatch, transport)

    with pytest.raises(MailUnavailable) as exc:
        await client.sync_mailbox(4)

    assert exc.value.status_code == 429
    assert len(transport.calls) == 1  # 5xx/429 на write не ретраятся


# --- §1.3 п.5: ретрай-политика ADR-038 §1 не сломана --------------------------
async def test_connect_error_is_retried_up_to_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeAggregatorTransport().on("DELETE", "/mailboxes/8", connect_errors=99)
    client = _client(monkeypatch, transport, read_timeout_sec=1.0, deadline_sec=10.0)

    with pytest.raises(MailUnavailable) as exc:
        await client.delete_mailbox(8)

    assert len(transport.calls) == 3  # 3 попытки (len(_BACKOFF_DELAYS_SEC) + 1)
    assert exc.value.status_code is None  # транспортная ветка — без статуса


async def test_connect_error_retry_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeAggregatorTransport().on(
        "POST", "/sync", connect_errors=1, json_body={"queued": True}
    )
    client = _client(monkeypatch, transport, read_timeout_sec=1.0, deadline_sec=10.0)

    assert await client.sync_mailbox(9) == {"queued": True}
    assert len(transport.calls) == 2


# --- §1.3 п.6: две фабрики клиентов по категориям путей ----------------------
def test_fast_client_factory_uses_fast_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "k")
    get_settings.cache_clear()
    settings = get_settings()

    fast = get_mail_client()

    assert fast._read_timeout_sec == settings.mail_api_timeout_sec
    assert fast._deadline_sec == settings.mail_api_deadline_sec


def test_mail_server_client_factory_uses_mailserver_budgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "k")
    get_settings.cache_clear()
    settings = get_settings()

    slow = get_mail_server_client()

    assert slow._read_timeout_sec == settings.mail_api_mailserver_timeout_sec
    assert slow._deadline_sec == settings.mail_api_mailserver_deadline_sec
    # Mail-server-бюджет строго больше быстрого — иначе законный долгий ответ агрегатора
    # (до 60 с его nginx) обрывался бы CRM'ом (прод-баг).
    assert slow._read_timeout_sec > settings.mail_api_timeout_sec
    assert slow._deadline_sec > slow._read_timeout_sec
