"""Unit-тесты сервиса почты `app/services/mail_service.py` (04-api.md#mail, ADR-013/ADR-017).

Клиент (httpx-граница к postapp.store) замокан `FakeMailClient` — реальных запросов
наружу нет. Проверяются: гейт `mail_enabled` ДО валидации `limit` (503 mail_not_configured);
диапазон `limit` 1..200 (400 validation_error); взаимоисключение режимов пагинации
(`since_id` при desc / `before_id` при asc → 400 ДО внешнего вызова); взаимоисключение
серверных фильтров `mail_account_id`/`group_id` (оба → 400 `field=filter` ЛОКАЛЬНО, без
внешнего вызова); проброс `order`/курсоров/фильтров во внешний клиент; нормализация курсоров
(незапрошенный курсор → null); справочники teams/mailboxes (503-гейт, успех, маппинг любой
ошибки клиента в 502); непустой `body` reply (422 unprocessable); маппинг исключений клиента
в коды CRM (404/422/502; внешний 400 на list → 400 validation_error); несовместимое тело → 502.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.config import Settings
from app.errors import AppError
from app.infra.mail_client import MailMessageNotFound, MailRejected, MailUnavailable
from app.schemas.mail import MailOrder, MailReplyRequest
from app.services.mail_service import MailService

_MESSAGE: dict[str, Any] = {
    "id": 1042,
    "subject": "Отчёт за июнь",
    "internal_date": "2026-07-02T09:15:00Z",
    "from_addr": "sender@example.com",
    "from_name": "Иван Петров",
    "to_addrs": "inbox@postapp.store",
    "cc_addrs": None,
    "mail_account": {"id": 3, "email": "inbox@postapp.store", "display_name": "Входящие"},
    "body_text": "тело",
    "body_html": "<p>тело</p>",
    "body_present": True,
    "body_truncated": False,
    "tags": [{"id": 7, "name": "важное", "color": "#EF4444"}],
}
# Внешний ответ desc-режима: несёт `next_before_id` (курсор догрузки старых).
_DESC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_before_id": 1001, "has_more": True}
# Внешний ответ asc-режима: несёт `next_since_id` (курсор keyset вперёд).
_ASC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_since_id": 1042, "has_more": True}
_VALID_REPLY: dict[str, Any] = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}
_TEAMS: dict[str, Any] = {"teams": [{"id": 3, "name": "Продажи"}]}
_MAILBOXES: dict[str, Any] = {
    "mailboxes": [
        {
            "id": 7,
            "email": "inbox@postapp.store",
            "display_name": "Входящие",
            "group_id": 3,
            "is_active": True,
        }
    ]
}

# Полный набор аргументов вызова list_messages — эталон для сравнения list_calls.
_NO_FILTER = {"mail_account_id": None, "group_id": None}


class FakeMailClient:
    """Замена MailClient: возвращает заготовку либо бросает заданное исключение.

    Сигнатура `list_messages` повторяет реальный клиент (все аргументы keyword-only,
    включая серверные фильтры `mail_account_id`/`group_id` — ADR-017); вызовы фиксируются
    целиком — чтобы проверить проброс `order`/курсоров/фильтров и что при локальной ошибке
    валидации внешний сервис не вызывается. teams/mailboxes — идемпотентные GET-справочники.
    """

    def __init__(
        self,
        *,
        list_result: dict[str, Any] | None = None,
        reply_result: dict[str, Any] | None = None,
        teams_result: dict[str, Any] | None = None,
        mailboxes_result: dict[str, Any] | None = None,
        list_exc: Exception | None = None,
        reply_exc: Exception | None = None,
        teams_exc: Exception | None = None,
        mailboxes_exc: Exception | None = None,
    ) -> None:
        self._list_result = list_result
        self._reply_result = reply_result
        self._teams_result = teams_result
        self._mailboxes_result = mailboxes_result
        self._list_exc = list_exc
        self._reply_exc = reply_exc
        self._teams_exc = teams_exc
        self._mailboxes_exc = mailboxes_exc
        self.list_calls: list[dict[str, Any]] = []
        self.reply_calls: list[tuple[int, dict[str, Any]]] = []
        self.teams_calls = 0
        self.mailboxes_calls = 0

    async def list_messages(
        self,
        *,
        order: str,
        since_id: int | None,
        before_id: int | None,
        limit: int,
        mail_account_id: int | None,
        group_id: int | None,
    ) -> dict[str, Any]:
        self.list_calls.append(
            {
                "order": order,
                "since_id": since_id,
                "before_id": before_id,
                "limit": limit,
                "mail_account_id": mail_account_id,
                "group_id": group_id,
            }
        )
        if self._list_exc is not None:
            raise self._list_exc
        assert self._list_result is not None
        return self._list_result

    async def reply(self, message_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.reply_calls.append((message_id, payload))
        if self._reply_exc is not None:
            raise self._reply_exc
        assert self._reply_result is not None
        return self._reply_result

    async def list_teams(self) -> dict[str, Any]:
        self.teams_calls += 1
        if self._teams_exc is not None:
            raise self._teams_exc
        assert self._teams_result is not None
        return self._teams_result

    async def list_mailboxes(self) -> dict[str, Any]:
        self.mailboxes_calls += 1
        if self._mailboxes_exc is not None:
            raise self._mailboxes_exc
        assert self._mailboxes_result is not None
        return self._mailboxes_result


def _settings(*, mail_api_key: str) -> Settings:
    return Settings(mail_api_key=mail_api_key)


def _service(client: FakeMailClient, *, enabled: bool) -> MailService:
    return MailService(client=client, settings=_settings(mail_api_key="k" if enabled else ""))


async def _list(
    service: MailService,
    *,
    order: MailOrder = "desc",
    since_id: int | None = None,
    before_id: int | None = None,
    limit: int = 50,
    mail_account_id: int | None = None,
    group_id: int | None = None,
) -> Any:
    return await service.list_messages(
        order=order,
        since_id=since_id,
        before_id=before_id,
        limit=limit,
        mail_account_id=mail_account_id,
        group_id=group_id,
    )


# ------------------------------------------------------------- гейт mail_enabled
async def test_list_disabled_returns_503_not_configured() -> None:
    client = FakeMailClient(list_result=_DESC_LIST)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=False))

    assert exc.value.status_code == 503
    assert exc.value.code == "mail_not_configured"
    assert client.list_calls == []  # внешний сервис не вызывался


async def test_reply_disabled_returns_503_not_configured() -> None:
    client = FakeMailClient(reply_result=_VALID_REPLY)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=False).reply(
            message_id=1, payload=MailReplyRequest(body="ответ")
        )

    assert exc.value.status_code == 503
    assert exc.value.code == "mail_not_configured"
    assert client.reply_calls == []


async def test_teams_disabled_returns_503_not_configured() -> None:
    client = FakeMailClient(teams_result=_TEAMS)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=False).list_teams()

    assert exc.value.status_code == 503
    assert exc.value.code == "mail_not_configured"
    assert client.teams_calls == 0  # гейт до внешнего вызова


async def test_mailboxes_disabled_returns_503_not_configured() -> None:
    client = FakeMailClient(mailboxes_result=_MAILBOXES)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=False).list_mailboxes()

    assert exc.value.status_code == 503
    assert exc.value.code == "mail_not_configured"
    assert client.mailboxes_calls == 0


async def test_gate_precedes_limit_validation() -> None:
    """При выключенной почте невалидный limit всё равно даёт 503, а не 400."""
    client = FakeMailClient(list_result=_DESC_LIST)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=False), limit=0)

    assert exc.value.status_code == 503
    assert exc.value.code == "mail_not_configured"


# ------------------------------------------------------------------ валидация limit
@pytest.mark.parametrize("limit", [0, -5, 201, 1000])
async def test_list_limit_out_of_range_returns_400(limit: int) -> None:
    client = FakeMailClient(list_result=_DESC_LIST)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True), limit=limit)

    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"
    assert client.list_calls == []


@pytest.mark.parametrize("limit", [1, 50, 200])
async def test_list_limit_boundaries_ok(limit: int) -> None:
    client = FakeMailClient(list_result=_DESC_LIST)
    result = await _list(_service(client, enabled=True), limit=limit)

    assert result.next_before_id == 1001
    assert client.list_calls == [
        {"order": "desc", "since_id": None, "before_id": None, "limit": limit, **_NO_FILTER}
    ]


# ----------------------------------------------------- desc-режим (основной, default)
async def test_list_desc_default_forwards_order_and_nulls_since_cursor() -> None:
    """order=desc (default) без before_id → внешний вызов order=desc без курсоров;
    ответ: next_since_id=null (принудительно), next_before_id из внешнего, has_more."""
    client = FakeMailClient(list_result=_DESC_LIST)
    result = await _list(_service(client, enabled=True))

    assert client.list_calls == [
        {"order": "desc", "since_id": None, "before_id": None, "limit": 50, **_NO_FILTER}
    ]
    assert result.next_since_id is None
    assert result.next_before_id == 1001
    assert result.has_more is True
    assert len(result.messages) == 1
    assert result.messages[0].id == 1042


async def test_list_desc_with_before_id_passes_through_and_forces_since_null() -> None:
    """order=desc&before_id=N → проброс before_id; next_since_id принудительно null."""
    client = FakeMailClient(list_result=_DESC_LIST)
    result = await _list(_service(client, enabled=True), before_id=1001)

    assert client.list_calls == [
        {"order": "desc", "since_id": None, "before_id": 1001, "limit": 50, **_NO_FILTER}
    ]
    assert result.next_since_id is None
    assert result.next_before_id == 1001


async def test_list_desc_null_next_before_id_empty_batch_is_valid() -> None:
    client = FakeMailClient(list_result={"messages": [], "next_before_id": None, "has_more": False})
    result = await _list(_service(client, enabled=True), before_id=500)

    assert result.messages == []
    assert result.next_before_id is None
    assert result.next_since_id is None
    assert result.has_more is False


# --------------------------------------------------------- asc-режим (совместимость)
async def test_list_asc_with_since_id_passes_through_and_forces_before_null() -> None:
    """order=asc&since_id=N → BC: проброс since_id; next_before_id принудительно null."""
    client = FakeMailClient(list_result=_ASC_LIST)
    result = await _list(_service(client, enabled=True), order="asc", since_id=1000)

    assert client.list_calls == [
        {"order": "asc", "since_id": 1000, "before_id": None, "limit": 50, **_NO_FILTER}
    ]
    assert result.next_before_id is None
    assert result.next_since_id == 1042
    assert result.has_more is True


async def test_list_asc_null_next_since_id_empty_batch_is_valid() -> None:
    client = FakeMailClient(list_result={"messages": [], "next_since_id": None, "has_more": False})
    result = await _list(_service(client, enabled=True), order="asc", since_id=5000)

    assert result.messages == []
    assert result.next_since_id is None
    assert result.next_before_id is None
    assert result.has_more is False


# --------------------------------------- серверные фильтры mail_account_id/group_id
async def test_list_forwards_mail_account_id_to_client() -> None:
    client = FakeMailClient(list_result=_DESC_LIST)
    await _list(_service(client, enabled=True), mail_account_id=7)

    assert client.list_calls == [
        {
            "order": "desc",
            "since_id": None,
            "before_id": None,
            "limit": 50,
            "mail_account_id": 7,
            "group_id": None,
        }
    ]


async def test_list_forwards_group_id_to_client() -> None:
    client = FakeMailClient(list_result=_DESC_LIST)
    await _list(_service(client, enabled=True), group_id=3)

    assert client.list_calls == [
        {
            "order": "desc",
            "since_id": None,
            "before_id": None,
            "limit": 50,
            "mail_account_id": None,
            "group_id": 3,
        }
    ]


async def test_list_both_filters_returns_400_field_filter_before_external() -> None:
    """mail_account_id И group_id одновременно → 400 validation_error (field=filter)
    ЛОКАЛЬНО, ДО внешнего вызова (04-api.md#mail, ADR-017)."""
    client = FakeMailClient(list_result=_DESC_LIST)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True), mail_account_id=7, group_id=3)

    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"
    assert exc.value.details[0]["field"] == "filter"
    assert client.list_calls == []  # внешний сервис не вызывался


async def test_list_filter_validation_precedes_pagination_and_limit() -> None:
    """Оба фильтра + валидный запрос: локальная валидация фильтров срабатывает и
    внешний вызов не происходит (пустой список нельзя вернуть без валидации)."""
    client = FakeMailClient(list_result=_DESC_LIST)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True), mail_account_id=1, group_id=2, before_id=100)

    assert exc.value.status_code == 400
    assert exc.value.details[0]["field"] == "filter"
    assert client.list_calls == []


# ---------------------------------------------------- нормализация курсоров (незапрошенный → null)
async def test_list_desc_normalizes_stray_since_cursor_from_external() -> None:
    """Даже если внешний API вернул оба курсора, desc-режим форсит next_since_id=null."""
    client = FakeMailClient(
        list_result={
            "messages": [_MESSAGE],
            "next_since_id": 9999,
            "next_before_id": 1001,
            "has_more": True,
        }
    )
    result = await _list(_service(client, enabled=True))

    assert result.next_since_id is None  # незапрошенный курсор обнулён
    assert result.next_before_id == 1001


async def test_list_asc_normalizes_stray_before_cursor_from_external() -> None:
    client = FakeMailClient(
        list_result={
            "messages": [_MESSAGE],
            "next_since_id": 1042,
            "next_before_id": 1001,
            "has_more": True,
        }
    )
    result = await _list(_service(client, enabled=True), order="asc", since_id=1)

    assert result.next_before_id is None  # незапрошенный курсор обнулён
    assert result.next_since_id == 1042


# ------------------------------------------------- взаимоисключение режимов пагинации
async def test_list_desc_with_since_id_returns_400_before_external() -> None:
    """order=desc&since_id=X → 400 validation_error (field=since_id) ДО внешнего вызова."""
    client = FakeMailClient(list_result=_DESC_LIST)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True), order="desc", since_id=1000)

    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"
    assert exc.value.details[0]["field"] == "since_id"
    assert client.list_calls == []  # локальная валидация — до внешнего вызова


async def test_list_asc_with_before_id_returns_400_before_external() -> None:
    """order=asc&before_id=X → 400 validation_error (field=before_id) ДО внешнего вызова."""
    client = FakeMailClient(list_result=_ASC_LIST)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True), order="asc", before_id=1001)

    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"
    assert exc.value.details[0]["field"] == "before_id"
    assert client.list_calls == []


# ---------------------------------------------------- несовместимое тело внешнего API
async def test_list_incompatible_body_maps_to_502() -> None:
    # Отсутствует обязательное поле has_more → схема не проходит → 502.
    client = FakeMailClient(list_result={"messages": [], "next_before_id": None})
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True))

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


# --------------------------------------------------------------- маппинг ошибок list
async def test_list_unavailable_maps_to_502() -> None:
    client = FakeMailClient(list_exc=MailUnavailable("down"))
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True))

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


async def test_list_external_400_maps_to_400_validation_error() -> None:
    """Внешний 400 (рассинхрон взаимоисключения) → 400 validation_error (04-api.md#mail)."""
    client = FakeMailClient(list_exc=MailRejected(400))
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True))

    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"


@pytest.mark.parametrize(
    "client_exc", [MailMessageNotFound("404"), MailRejected(409), MailRejected(422)]
)
async def test_list_other_client_error_maps_to_502(client_exc: Exception) -> None:
    client = FakeMailClient(list_exc=client_exc)
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=True))

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


# --------------------------------------------------------------- teams (ADR-017)
async def test_list_teams_success_returns_schema() -> None:
    client = FakeMailClient(teams_result=_TEAMS)
    result = await _service(client, enabled=True).list_teams()

    assert client.teams_calls == 1
    assert len(result.teams) == 1
    assert result.teams[0].id == 3
    assert result.teams[0].name == "Продажи"


async def test_list_teams_empty_is_valid() -> None:
    client = FakeMailClient(teams_result={"teams": []})
    result = await _service(client, enabled=True).list_teams()

    assert result.teams == []


@pytest.mark.parametrize(
    "client_exc",
    [MailUnavailable("down"), MailRejected(400), MailMessageNotFound("404")],
)
async def test_list_teams_any_client_error_maps_to_502(client_exc: Exception) -> None:
    """teams допускает только 502/503: любая ошибка клиента (в т.ч. неожиданный 404/400)
    сводится к 502 (04-api.md#mail)."""
    client = FakeMailClient(teams_exc=client_exc)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_teams()

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


async def test_list_teams_incompatible_body_maps_to_502() -> None:
    client = FakeMailClient(teams_result={"unexpected": "shape"})
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_teams()

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


# --------------------------------------------------------------- mailboxes (ADR-017)
async def test_list_mailboxes_success_returns_schema() -> None:
    client = FakeMailClient(mailboxes_result=_MAILBOXES)
    result = await _service(client, enabled=True).list_mailboxes()

    assert client.mailboxes_calls == 1
    assert len(result.mailboxes) == 1
    mb = result.mailboxes[0]
    assert mb.id == 7
    assert mb.email == "inbox@postapp.store"
    assert mb.display_name == "Входящие"
    assert mb.group_id == 3
    assert mb.is_active is True


async def test_list_mailboxes_empty_is_valid() -> None:
    client = FakeMailClient(mailboxes_result={"mailboxes": []})
    result = await _service(client, enabled=True).list_mailboxes()

    assert result.mailboxes == []


async def test_list_mailboxes_nullable_fields_accepted() -> None:
    """display_name и group_id допускают null (04-api.md#mail)."""
    client = FakeMailClient(
        mailboxes_result={
            "mailboxes": [
                {
                    "id": 9,
                    "email": "team@postapp.store",
                    "display_name": None,
                    "group_id": None,
                    "is_active": False,
                }
            ]
        }
    )
    result = await _service(client, enabled=True).list_mailboxes()

    mb = result.mailboxes[0]
    assert mb.display_name is None
    assert mb.group_id is None
    assert mb.is_active is False


@pytest.mark.parametrize(
    "client_exc",
    [MailUnavailable("down"), MailRejected(400), MailMessageNotFound("404")],
)
async def test_list_mailboxes_any_client_error_maps_to_502(client_exc: Exception) -> None:
    client = FakeMailClient(mailboxes_exc=client_exc)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_mailboxes()

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


async def test_list_mailboxes_incompatible_body_maps_to_502() -> None:
    client = FakeMailClient(mailboxes_result={"mailboxes": [{"id": 1}]})
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_mailboxes()

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


# ----------------------------------------------------------------- валидация reply
@pytest.mark.parametrize("body", ["", "   ", "\n\t "])
async def test_reply_empty_or_whitespace_body_returns_422(body: str) -> None:
    client = FakeMailClient(reply_result=_VALID_REPLY)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).reply(
            message_id=1, payload=MailReplyRequest(body=body)
        )

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"
    assert client.reply_calls == []  # некорректное тело наружу не уходит


async def test_reply_success_returns_schema_and_excludes_none_fields() -> None:
    client = FakeMailClient(reply_result=_VALID_REPLY)
    result = await _service(client, enabled=True).reply(
        message_id=42, payload=MailReplyRequest(body="Спасибо, получил.")
    )

    assert result.sent_id == 5099
    assert result.smtp_message_id == "<abc123@postapp.store>"
    # exclude_none: незаданные to/cc/subject не отправляются во внешний сервис.
    assert client.reply_calls[0][0] == 42
    sent_payload = client.reply_calls[0][1]
    assert sent_payload == {"body": "Спасибо, получил."}
    assert "to" not in sent_payload
    assert "cc" not in sent_payload


# --------------------------------------------------------------- маппинг ошибок reply
async def test_reply_not_found_maps_to_404() -> None:
    client = FakeMailClient(reply_exc=MailMessageNotFound("404"))
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).reply(message_id=1, payload=MailReplyRequest(body="x"))

    assert exc.value.status_code == 404
    assert exc.value.code == "mail_message_not_found"


async def test_reply_rejected_maps_to_422() -> None:
    client = FakeMailClient(reply_exc=MailRejected(400))
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).reply(message_id=1, payload=MailReplyRequest(body="x"))

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


async def test_reply_unavailable_maps_to_502() -> None:
    client = FakeMailClient(reply_exc=MailUnavailable("down"))
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).reply(message_id=1, payload=MailReplyRequest(body="x"))

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"
