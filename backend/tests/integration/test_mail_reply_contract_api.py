"""Integration (ADR-057, TD-062): контракт отправки письма — `sent_id` выдаёт CRM.

Регресс ЖИВОГО прод-бага: CRM парсила сырой ответ агрегатора ПУБЛИЧНОЙ схемой, где
`sent_id: int` был обязателен, а агрегатор его больше не выдаёт (его `sent_messages`
дропается) — reply падал. Кросс-репозиторный контракт отправки не был покрыт ни одним
тестом (TD-062), поэтому здесь он закрывается машинно:

- §1/§5.3: `sent_id` — `uuid` РЕАЛЬНО созданной строки `mail_sent_messages` (проверяется
  ПО БД, а не по телу ответа); `smtp_message_id` — `string | null`;
- §2: ответ агрегатора парсится ВНУТРЕННЕЙ схемой `{smtp_message_id?}` — тело СО старым
  лишним `sent_id` и БЕЗ него принимаются одинаково (машинная защита порядка деплоя
  «агрегатор выкатывается первым»);
- §5: `200` без `smtp_message_id` (и с явным `null`) → CRM отдаёт `200`, строка факта
  отправки СОЗДАНА (значение `NULL`), в логе — warning. Раньше письмо уходило, а записи
  о нём не оставалось вовсе — необратимая потеря аудита;
- §6: нормативный НАБОР ПОЛЕЙ логов reply (ассертится набор, а не текст);
- §3: внешний `404` от `send` → `404 mail_mailbox_not_found` (рассинхрон каталога), при
  этом СОБСТВЕННАЯ проверка CRM (письма нет / вне `MailScope`) по-прежнему даёт
  `404 mail_message_not_found` — анти-энумерация не сломана;
- ADR-053: ветки `422`/`502 smtp_failed`/`504` при отправке не потеряны; при таймауте
  ретрая НЕТ (иначе адресат получит дубль) и строка факта отправки не создаётся.

Агрегатор мокается на уровне httpx-ТРАНСПОРТА (`FakeAggregatorTransport`), а не подменой
клиента: только так проверяются фактическое ТЕЛО ответа (в т.ч. его отсутствующие поля),
число исходящих запросов и бюджеты. Контракт сверяется по `error.code` ФАКТИЧЕСКОГО
ответа, а не по тексту `message`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.infra.mail_client import MailClient
from mail_s34_helpers import (
    add_membership,
    build_app,
    build_principal,
    client,
    dt,
    mail_db,
    seed_account,
    seed_message,
    seed_role,
    seed_team,
    seed_user,
)
from mail_transport_helpers import FakeAggregatorTransport, error_body, install_transport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_BASE = "https://postapp.example"
_ACCOUNT_ID = 1
_MESSAGE_ID = 1

# Бюджеты mail-server-категории (прод 75/85 — ADR-053 §1.1) в суб-секундном масштабе.
_SLOW_READ = 0.4
_SLOW_DEADLINE = 1.0
_FAST_READ = 0.05
_FAST_DEADLINE = 0.6

# Тело нового агрегатора (ADR-057 §2) и СТАРОГО (с лишним `sent_id`) — оба обязаны пройти.
_NEW_BODY = {"smtp_message_id": "<abc123@example.com>"}
_LEGACY_BODY = {"sent_id": 42, "smtp_message_id": "<abc123@example.com>"}


class LogSpy:
    """Спай structlog-логгера сервиса (набор полей события — нормативен, ADR-057 §6).

    `structlog.testing.capture_logs()` здесь не годится: `configure_logging` включает
    `cache_logger_on_first_use=True` (`app/logging.py:64`), а `mail_service.logger` —
    модульный прокси, который мог закешировать bound-логгер в другом тесте процесса.
    Подмена самого `logger` даёт детерминированный перехват без зависимости от
    глобального состояния structlog (изоляция теста от порядка прогона).
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


async def _enable_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAIL_API_KEY", "test-key")
    get_settings.cache_clear()


def _fast_client() -> MailClient:
    return MailClient(
        base_url=_BASE, api_key="test-key", read_timeout_sec=_FAST_READ, deadline_sec=_FAST_DEADLINE
    )


def _slow_client() -> MailClient:
    """Клиент MAIL-SERVER категории — ею идёт reply (ADR-053 §1.1)."""
    return MailClient(
        base_url=_BASE, api_key="test-key", read_timeout_sec=_SLOW_READ, deadline_sec=_SLOW_DEADLINE
    )


def _app(sm: async_sessionmaker[AsyncSession], principal: Any = None) -> Any:
    return build_app(
        sm,
        principal if principal is not None else build_principal(is_superadmin=True),
        mail_client=_fast_client(),
        mail_server_client=_slow_client(),
    )


async def _seed_message(sm: async_sessionmaker[AsyncSession]) -> None:
    async with sm() as s:
        team = await seed_team(s)
        await seed_account(s, account_id=_ACCOUNT_ID, team_id=team.id)
        await seed_message(
            s,
            account_id=_ACCOUNT_ID,
            uid=1,
            internal_date=dt(),
            subject="Исходная",
            from_addr="orig@example.com",
            message_id_header="<orig@example.com>",
        )
        await s.commit()


async def _sent_rows(sm: async_sessionmaker[AsyncSession]) -> list[Any]:
    """Реально созданные строки `mail_sent_messages` — источник истины для `sent_id`."""
    from app.models.mail_sent_message import MailSentMessage

    async with sm() as s:
        result = await s.execute(select(MailSentMessage))
        return list(result.scalars().all())


def _reply_transport(**kwargs: Any) -> FakeAggregatorTransport:
    return FakeAggregatorTransport().on("POST", "/send", **kwargs)


# =============================================================================
# §1/§2 — `sent_id` = id РЕАЛЬНОЙ строки CRM; совместимость с порядком деплоя
# =============================================================================


@pytest.mark.parametrize(
    ("label", "body"),
    [("new_aggregator", _NEW_BODY), ("legacy_aggregator_with_extra_sent_id", _LEGACY_BODY)],
)
async def test_reply_sent_id_is_uuid_of_real_crm_row_for_both_response_shapes(
    monkeypatch: pytest.MonkeyPatch, label: str, body: dict[str, Any]
) -> None:
    """§1/§2: `sent_id` ответа CRM = `mail_sent_messages.id` (uuid) РЕАЛЬНО созданной строки.

    Оба тела ответа агрегатора парсятся ВНУТРЕННЕЙ схемой: новое `{smtp_message_id}` и
    старое `{sent_id, smtp_message_id}` (лишнее поле игнорируется). Это машинная защита
    порядка деплоя — агрегатор выкатывается ПЕРВЫМ, и CRM обязана пережить оба тела.
    `sent_id` агрегатора (`42`) наружу НЕ протекает: он не является идентификатором CRM.
    """
    await _enable_mail(monkeypatch)
    transport = _reply_transport(json_body=body)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})

        assert resp.status_code == 200, resp.text
        payload = resp.json()
        rows = await _sent_rows(sm)

    assert len(rows) == 1, f"{label}: факт отправки обязан быть записан ровно один раз"
    row = rows[0]
    # Публичный `sent_id` — uuid СВОЕЙ строки, а не число агрегатора (ADR-057 §1).
    assert payload["sent_id"] == str(row.id)
    assert uuid.UUID(payload["sent_id"]).version == 4
    assert payload["smtp_message_id"] == "<abc123@example.com>"
    assert row.smtp_message_id == "<abc123@example.com>"
    assert row.mail_account_id == _ACCOUNT_ID
    assert row.to_addrs == "orig@example.com"  # дефолт: from исходного письма
    assert row.body_text == "ответ"
    assert row.in_reply_to == "<orig@example.com>"


# =============================================================================
# §5 — сохранность аудита: `200` без `smtp_message_id` НЕ уничтожает запись
# =============================================================================


@pytest.mark.parametrize(
    ("label", "body"), [("field_absent", {}), ("explicit_null", {"smtp_message_id": None})]
)
async def test_reply_200_without_smtp_message_id_persists_sent_row_and_returns_200(
    monkeypatch: pytest.MonkeyPatch, label: str, body: dict[str, Any]
) -> None:
    """§5: агрегатор ответил `200` без идентификатора → CRM `200`, строка СОЗДАНА (`NULL`).

    РЕГРЕСС-ГЕЙТ: строгая схема давала `502` ПОСЛЕ уже совершённой (необратимой)
    SMTP-отправки — письмо ушло, записи о нём не оставалось, и пользователь отправлял
    дубль. Схема не имеет права быть строже модели данных (колонка nullable).
    """
    await _enable_mail(monkeypatch)
    transport = _reply_transport(json_body=body)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})

        assert resp.status_code == 200, f"{label}: {resp.text}"  # НЕ 502
        payload = resp.json()
        rows = await _sent_rows(sm)

    assert len(rows) == 1, f"{label}: факт УЖЕ отправленного письма потерян"
    assert rows[0].smtp_message_id is None
    assert payload["smtp_message_id"] is None  # публичный тип — string | null (§5.3)
    assert payload["sent_id"] == str(rows[0].id)  # `sent_id` есть ВСЕГДА


async def test_reply_missing_smtp_message_id_logs_warning_with_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§5.4/§6: неполный ответ агрегатора — НАБЛЮДАЕМОЕ событие; ассертится НАБОР ПОЛЕЙ."""
    await _enable_mail(monkeypatch)
    install_transport(monkeypatch, _reply_transport(json_body={}))
    spy = _spy_service_logger(monkeypatch)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 200, resp.text
    assert "mail_send_missing_smtp_message_id" in spy.names()
    fields = spy.payload("mail_send_missing_smtp_message_id")
    assert set(fields) == {"mail_account_id", "message_id", "sent_id"}
    assert fields["mail_account_id"] == _ACCOUNT_ID
    assert fields["message_id"] == _MESSAGE_ID
    # `sent_id` в логе — id РЕАЛЬНОЙ строки: по логу можно найти запись об отправке.
    assert str(fields["sent_id"]) == str(rows[0].id)


async def test_reply_with_smtp_message_id_does_not_log_missing_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нормальный случай не шумит: warning — только при фактическом отсутствии поля."""
    await _enable_mail(monkeypatch)
    install_transport(monkeypatch, _reply_transport(json_body=_NEW_BODY))
    spy = _spy_service_logger(monkeypatch)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})

    assert resp.status_code == 200, resp.text
    assert "mail_send_missing_smtp_message_id" not in spy.names()


async def test_reply_garbage_smtp_message_id_type_is_502_and_writes_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Граница толерантности (§5): терпимо ОТСУТСТВИЕ поля, а не мусор в нём.

    Тело с `smtp_message_id: 123` (не строка) — несовместимый ответ → `502 mail_unavailable`
    (`_parse`). Записи не появляется: «письмо ушло» этим телом не подтверждено.
    """
    await _enable_mail(monkeypatch)
    install_transport(monkeypatch, _reply_transport(json_body={"smtp_message_id": 123}))

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"
    assert rows == []


# =============================================================================
# §3/§6 — внешний `404` = «ЯЩИКА нет»; собственная проверка CRM = «письма нет»
# =============================================================================


async def test_reply_external_404_maps_to_mail_mailbox_not_found_with_log_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3: `404` от `send` = рассинхрон каталога ящиков → `404 mail_mailbox_not_found`.

    Прежний маппинг в `mail_message_not_found` ЛГАЛ пользователю (письмо в CRM есть).
    §6: warning несёт обязательный набор полей — иначе по логу не понять, КАКОЙ ящик сломан.
    """
    await _enable_mail(monkeypatch)
    install_transport(monkeypatch, _reply_transport(status=404, json_body=error_body("not_found")))
    spy = _spy_service_logger(monkeypatch)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_mailbox_not_found"  # НЕ mail_message_not_found
    assert rows == []  # отправки не было — факта отправки нет
    assert "mail_reply_mailbox_missing_in_aggregator" in spy.names()
    fields = spy.payload("mail_reply_mailbox_missing_in_aggregator")
    assert set(fields) == {"mail_account_id", "message_id"}
    assert fields["mail_account_id"] == _ACCOUNT_ID
    assert fields["message_id"] == _MESSAGE_ID


async def test_reply_unknown_message_still_mail_message_not_found_without_aggregator_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3: код «письма нет» остаётся за СОБСТВЕННОЙ проверкой CRM — агрегатор не зовётся."""
    await _enable_mail(monkeypatch)
    transport = _reply_transport(json_body=_NEW_BODY)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post("/api/mail/messages/424242/reply", json={"body": "ответ"})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_message_not_found"
    assert transport.calls == []


async def test_reply_out_of_scope_message_404_message_not_found_anti_enumeration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3 + анти-энумерация: чужое письмо неотличимо от несуществующего (`mail_message_not_found`).

    Переклассификация внешнего `404` в «ящика нет» НЕ должна была ослабить маскировку:
    для письма ВНЕ `MailScope` код обязан остаться `mail_message_not_found`, иначе по
    ответу можно перебирать чужие письма.
    """
    await _enable_mail(monkeypatch)
    transport = _reply_transport(json_body=_NEW_BODY)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            user = await seed_user(s, role)
            my_team = await seed_team(s)
            other_team = await seed_team(s)
            await add_membership(s, user.id, my_team.id)
            await seed_account(s, account_id=_ACCOUNT_ID, team_id=other_team.id)
            await seed_message(s, account_id=_ACCOUNT_ID, uid=1, internal_date=dt())
            await s.commit()
            user_id = user.id

        principal = build_principal(
            user_id=user_id, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        async with client(_app(sm, principal)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "mail_message_not_found"
    assert transport.calls == []  # до агрегатора запрос не доходит
    assert rows == []


# =============================================================================
# ADR-053 — ветки отказов отправки не потеряны амендментом ADR-057
# =============================================================================


@pytest.mark.parametrize(
    ("aggregator_code", "expected_code"),
    [
        ("smtp_login_failed", "mail_smtp_failed"),
        ("imap_login_failed", "mail_imap_failed"),
        ("something_else", "unprocessable"),  # fallback прежний
    ],
)
async def test_reply_aggregator_422_preserves_reason(
    monkeypatch: pytest.MonkeyPatch, aggregator_code: str, expected_code: str
) -> None:
    """ADR-053 §2: истинная причина отказа `422` доходит до пользователя (не `502`)."""
    await _enable_mail(monkeypatch)
    install_transport(
        monkeypatch, _reply_transport(status=422, json_body=error_body(aggregator_code))
    )

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == expected_code
    assert rows == []


async def test_reply_aggregator_502_smtp_failed_returns_mail_send_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-053 §2: `502 smtp_failed` = удалённый SMTP отклонил письмо, агрегатор РАБОТАЛ."""
    await _enable_mail(monkeypatch)
    install_transport(
        monkeypatch, _reply_transport(status=502, json_body=error_body("smtp_failed"))
    )

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_send_failed"  # НЕ mail_unavailable
    assert rows == []


async def test_reply_aggregator_503_maps_to_mail_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enable_mail(monkeypatch)
    install_transport(monkeypatch, _reply_transport(status=503))

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "mail_unavailable"


async def test_reply_own_timeout_504_without_retry_and_without_sent_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-053 §2/§3: собственный таймаут CRM на mail-server-пути → `504 mail_timeout`.

    Отправка НЕ повторяется (ровно один исходящий запрос) — ретрай при неопределённом
    состоянии SMTP дал бы адресату ДУБЛЬ письма. Факт отправки не подтверждён ⇒ строки
    `mail_sent_messages` нет (её пишет только подтверждённый `200`, ADR-057 §2.3).
    """
    await _enable_mail(monkeypatch)
    transport = _reply_transport(delay_sec=3.0)  # молчит дольше read-бюджета
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "mail_timeout"
    assert len(transport.calls) == 1  # анти-двойная-отправка
    assert rows == []


async def test_reply_aggregator_504_returns_504_mail_timeout_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-053 §2.1: `504` ОТ агрегатора → `504 mail_timeout`; ретрая нет, записи нет."""
    await _enable_mail(monkeypatch)
    transport = _reply_transport(status=504)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})
        rows = await _sent_rows(sm)

    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "mail_timeout"
    assert len(transport.calls) == 1
    assert rows == []


# =============================================================================
# §2 — исходящий запрос CRM к агрегатору (путь и тело) не изменился
# =============================================================================


async def test_reply_calls_mailbox_scoped_send_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2: CRM зовёт `POST /api/external/mailboxes/{mail_accounts.id}/send` — путь не менялся."""
    await _enable_mail(monkeypatch)
    transport = _reply_transport(json_body=_NEW_BODY)
    install_transport(monkeypatch, transport)

    async with mail_db() as sm:
        await _seed_message(sm)
        async with client(_app(sm)) as c:
            resp = await c.post(f"/api/mail/messages/{_MESSAGE_ID}/reply", json={"body": "ответ"})

    assert resp.status_code == 200, resp.text
    assert transport.calls == [("POST", f"/api/external/mailboxes/{_ACCOUNT_ID}/send")]
