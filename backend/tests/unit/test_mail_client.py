"""Unit-тесты httpx-клиента почты `app/infra/mail_client.py` (modules/mail, ADR-012/038).

Внешний сервис `postapp.store` НЕ вызывается вживую: httpx-граница замокана через
`httpx.MockTransport` (паттерн `test_ai_provider.py`/`test_infra_health_logging.py`).
Проверяются: секрет `MAIL_API_KEY` только в заголовке `X-API-Key` и не в URL/логах
(05-security.md); проброс `order` во внешний API всегда явно, `since_id` — только при
`order=asc`, `before_id` — только при `order=desc`; серверные фильтры `mail_account_id`/
`group_id` (external ADR-0039 §3) **AND-комбинируемы** — передаются вместе; повторяемый
`group_id` из `group_ids`; справочники teams/mailboxes/tags (GET, идемпотентны);
идемпотентность ретраев (GET ретраит `{429,500,502,503,504}`+connect+read-timeout; write —
только connect; read-timeout/5xx на write НЕ ретраятся — защита от двойной записи);
постатусный маппинг внешних кодов (429/5xx → MailUnavailable; прочий 4xx → MailRejected
со `status_code`); несовместимое тело → MailUnavailable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import structlog
from app.infra import mail_client as mod
from app.infra.mail_client import (
    MailClient,
    MailRejected,
    MailUnavailable,
)

KEY = "mail-secret-KEY-abc123XYZ"

_VALID_LIST = {"messages": [], "next_before_id": None, "has_more": False}
_VALID_REPLY = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}
_VALID_TEAMS = {"teams": [{"id": 3, "name": "Продажи"}]}
_VALID_MAILBOXES = {
    "mailboxes": [
        {
            "id": 7,
            "email": "inbox@postapp.store",
            "display_name": "Входящие",
            "group_id": 3,
            "is_active": True,
            "last_synced_at": "2026-07-09T08:00:00Z",
            "last_sync_error": None,
            "consecutive_failures": 0,
        }
    ]
}
_VALID_TAGS = {"tags": []}


def _install(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    """Подменяет httpx.AsyncClient на клиент с MockTransport и глушит backoff-sleep."""
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)


def _client() -> MailClient:
    return MailClient(base_url="https://postapp.store", api_key=KEY, timeout_sec=5)


async def _list(client: MailClient, **overrides: Any) -> dict[str, Any]:
    """Вызов `list_messages` с дефолтами keyword-only параметров (ADR-038:
    `group_ids` — повторяемый; `mail_account_id`+`group_ids` комбинируемы)."""
    params: dict[str, Any] = {
        "order": "desc",
        "since_id": None,
        "before_id": None,
        "limit": 50,
        "mail_account_id": None,
        "group_ids": None,
    }
    params.update(overrides)
    return await client.list_messages(**params)


# --------------------------------------------------------- секрет: заголовок, не в URL
async def test_list_sends_api_key_header_and_key_not_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["x_api_key"] = request.headers.get("x-api-key")
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))

    result = await _list(_client(), before_id=100)

    assert result == _VALID_LIST
    assert captured["x_api_key"] == KEY  # ключ — только в X-API-Key
    assert captured["authorization"] is None
    assert KEY not in str(captured["url"])  # ключ не попадает в URL/query


async def test_reply_sends_api_key_header_and_key_not_in_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["x_api_key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=_VALID_REPLY)

    _install(monkeypatch, httpx.MockTransport(handler))

    result = await _client().reply(message_id=42, payload={"body": "текст"})

    assert result == _VALID_REPLY
    assert captured["x_api_key"] == KEY
    assert KEY not in str(captured["url"])


# ---------------------------------------------- проброс order/курсоров/limit в query
async def test_list_desc_forwards_order_and_before_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["order"] = request.url.params.get("order")
        captured["before_id"] = request.url.params.get("before_id")
        captured["limit"] = request.url.params.get("limit")
        captured["has_since"] = "since_id" in request.url.params
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client(), before_id=1001, limit=25)

    assert captured["path"] == "/api/external/messages"
    assert captured["order"] == "desc"
    assert captured["before_id"] == "1001"
    assert captured["limit"] == "25"
    assert captured["has_since"] is False  # since_id не пробрасывается в desc


async def test_list_desc_omits_before_id_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["order"] = request.url.params.get("order")
        captured["has_before"] = "before_id" in request.url.params
        captured["has_since"] = "since_id" in request.url.params
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client())

    assert captured["order"] == "desc"  # order передаётся всегда явно
    assert captured["has_before"] is False
    assert captured["has_since"] is False


async def test_list_asc_forwards_order_and_since_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["order"] = request.url.params.get("order")
        captured["since_id"] = request.url.params.get("since_id")
        captured["has_before"] = "before_id" in request.url.params
        return httpx.Response(200, json={"messages": [], "next_since_id": None, "has_more": False})

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client(), order="asc", since_id=777)

    assert captured["order"] == "asc"
    assert captured["since_id"] == "777"
    assert captured["has_before"] is False  # before_id не пробрасывается в asc


async def test_list_asc_omits_since_id_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["order"] = request.url.params.get("order")
        captured["has_since"] = "since_id" in request.url.params
        return httpx.Response(200, json={"messages": [], "next_since_id": None, "has_more": False})

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client(), order="asc")

    assert captured["order"] == "asc"
    assert captured["has_since"] is False


# --------------------------------- серверные фильтры mail_account_id/group_ids (ADR-038)
async def test_list_forwards_mail_account_id_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["mail_account_id"] = request.url.params.get("mail_account_id")
        captured["has_group"] = "group_id" in request.url.params
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client(), mail_account_id=7)

    assert captured["mail_account_id"] == "7"
    assert captured["has_group"] is False  # group_ids не задан → не уходит


async def test_list_forwards_group_ids_as_repeated_param(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["group_ids"] = request.url.params.get_list("group_id")
        captured["has_mail_account"] = "mail_account_id" in request.url.params
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client(), group_ids=[3, 5])

    assert captured["group_ids"] == ["3", "5"]  # повторяемый query-параметр group_id
    assert captured["has_mail_account"] is False


async def test_list_omits_both_filters_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["has_mail_account"] = "mail_account_id" in request.url.params
        captured["has_group"] = "group_id" in request.url.params
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client())

    assert captured["has_mail_account"] is False
    assert captured["has_group"] is False


async def test_list_forwards_both_filters_and_combined(monkeypatch: pytest.MonkeyPatch) -> None:
    """AND-комбинирование (ADR-038, взаимоисключение ADR-0037 снято): при обоих
    фильтрах во внешний API уходят и `mail_account_id`, и повторяемый `group_id`."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["mail_account_id"] = request.url.params.get("mail_account_id")
        captured["group_ids"] = request.url.params.get_list("group_id")
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _list(_client(), mail_account_id=7, group_ids=[3])

    assert captured["mail_account_id"] == "7"
    assert captured["group_ids"] == ["3"]


# ------------------------------------------ справочники teams/mailboxes/tags (GET)
async def test_list_teams_hits_teams_path_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["x_api_key"] = request.headers.get("x-api-key")
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_VALID_TEAMS)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().list_teams()

    assert result == _VALID_TEAMS
    assert captured["path"] == "/api/external/teams"
    assert captured["method"] == "GET"
    assert captured["x_api_key"] == KEY
    assert KEY not in str(captured["url"])


async def test_list_mailboxes_hits_mailboxes_path_with_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        captured["x_api_key"] = request.headers.get("x-api-key")
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_VALID_MAILBOXES)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().list_mailboxes()

    assert result == _VALID_MAILBOXES
    assert captured["path"] == "/api/external/mailboxes"
    assert captured["method"] == "GET"
    assert captured["x_api_key"] == KEY
    assert KEY not in str(captured["url"])


async def test_list_mailboxes_forwards_is_active_and_group_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["is_active"] = request.url.params.get("is_active")
        captured["group_ids"] = request.url.params.get_list("group_id")
        return httpx.Response(200, json=_VALID_MAILBOXES)

    _install(monkeypatch, httpx.MockTransport(handler))
    await _client().list_mailboxes(is_active=True, group_ids=[3, 8])

    assert captured["is_active"] == "true"
    assert captured["group_ids"] == ["3", "8"]


async def test_list_tags_hits_tags_path_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["method"] = request.method
        return httpx.Response(200, json=_VALID_TAGS)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().list_tags()

    assert result == _VALID_TAGS
    assert captured["path"] == "/api/external/tags"
    assert captured["method"] == "GET"


async def test_list_teams_retries_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """teams — идемпотентный GET: ретраит транзиентные, как list_messages."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=_VALID_TEAMS)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().list_teams()

    assert result == _VALID_TEAMS
    assert calls["n"] == 3


async def test_list_mailboxes_exhausts_5xx_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().list_mailboxes()

    assert calls["n"] == 3


# ----------------------------------------------- list (GET): ретраит транзиентные
async def test_list_retries_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _list(_client())

    assert result == _VALID_LIST
    assert calls["n"] == 3  # 1 попытка + 2 ретрая


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
async def test_list_retries_each_transient_status(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status_code)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _list(_client())

    assert calls["n"] == 3  # все транзиентные ретраятся до исчерпания


async def test_list_retries_read_timeout_then_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _list(_client())

    assert calls["n"] == 3  # read-timeout ретраится для идемпотентного GET


async def test_list_retries_connect_error_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, json=_VALID_LIST)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _list(_client())

    assert result == _VALID_LIST
    assert calls["n"] == 2


# ----------------------- write (POST/PATCH/DELETE): НЕ ретраит read-timeout/5xx ------
# Каждый write-метод обёрнут в фабрику вызова; проверяем единую политику ретраев.
_WRITE_CALLS: dict[str, Callable[[MailClient], Awaitable[dict[str, Any]]]] = {
    "reply": lambda c: c.reply(message_id=1, payload={"body": "x"}),
    "test_mailbox": lambda c: c.test_mailbox({"email": "a@b.c"}),
    "create_mailbox": lambda c: c.create_mailbox({"email": "a@b.c"}),
    "update_mailbox": lambda c: c.update_mailbox(1, {"is_active": False}),
    "delete_mailbox": lambda c: c.delete_mailbox(1),
    "sync_mailbox": lambda c: c.sync_mailbox(1),
    "create_tag": lambda c: c.create_tag({"name": "t", "color": "#2563eb"}),
    "update_tag": lambda c: c.update_tag(1, {"name": "t"}),
    "delete_tag": lambda c: c.delete_tag(1),
    "create_tag_rule": lambda c: c.create_tag_rule(1, {"type": "subject_contains", "pattern": "x"}),
    "delete_tag_rule": lambda c: c.delete_tag_rule(1, 2),
    "apply_tag_to_existing": lambda c: c.apply_tag_to_existing(1),
}


@pytest.mark.parametrize("name", list(_WRITE_CALLS))
async def test_write_does_not_retry_read_timeout(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Read-timeout на write: запрос мог уйти → повтор недопустим (без двойной записи)."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _WRITE_CALLS[name](_client())

    assert calls["n"] == 1  # ровно одна попытка


@pytest.mark.parametrize("name", list(_WRITE_CALLS))
async def test_write_does_not_retry_5xx(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _WRITE_CALLS[name](_client())

    assert calls["n"] == 1  # 5xx на write не ретраится


async def test_write_retries_connect_error_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # Соединение не установлено → запрос не отправлен → повтор безопасен.
            raise httpx.ConnectError("conn refused", request=request)
        return httpx.Response(200, json=_VALID_REPLY)

    _install(monkeypatch, httpx.MockTransport(handler))
    result = await _client().reply(message_id=1, payload={"body": "x"})

    assert result == _VALID_REPLY
    assert calls["n"] == 2


async def test_write_retries_connect_timeout_exhausts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("conn timeout", request=request)

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _client().create_mailbox({"email": "a@b.c"})

    assert calls["n"] == 3  # connect-таймаут ретраится (запрос не ушёл)


# --------------------------------------------- write: путь/метод/тело ----------------
async def test_create_mailbox_uses_post_and_forwards_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(201, json=_VALID_MAILBOXES["mailboxes"][0])

    _install(monkeypatch, httpx.MockTransport(handler))
    await _client().create_mailbox({"email": "a@b.c", "password": "sekret"})

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/external/mailboxes"
    assert b"sekret" in captured["body"]  # тело (транзитный пароль) уходит в запрос


async def test_delete_mailbox_204_returns_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(204)

    _install(monkeypatch, httpx.MockTransport(handler))
    assert await _client().delete_mailbox(7) == {}  # 204 → {}


# ------------------------------------------------------------------ маппинг статусов
async def test_reply_404_raises_rejected_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Постатусный клиент (ADR-038): 404 → MailRejected(404); контекст маппит сервис."""
    _install(monkeypatch, httpx.MockTransport(lambda _r: httpx.Response(404, json={"d": "no"})))
    with pytest.raises(MailRejected) as exc:
        await _client().reply(message_id=999, payload={"body": "x"})

    assert exc.value.status_code == 404


@pytest.mark.parametrize("status_code", [400, 403, 404, 409, 422])
async def test_other_4xx_raises_rejected(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    _install(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(status_code, json={"d": "bad"})),
    )
    with pytest.raises(MailRejected) as exc:
        await _list(_client())

    assert exc.value.status_code == status_code  # статус несётся для маппинга в сервисе


async def test_404_group_not_found_carries_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """404 с телом `{"error":{"code":"group_not_found"}}` → MailRejected несёт error_code."""
    _install(
        monkeypatch,
        httpx.MockTransport(
            lambda _r: httpx.Response(404, json={"error": {"code": "group_not_found"}})
        ),
    )
    with pytest.raises(MailRejected) as exc:
        await _list(_client())

    assert exc.value.status_code == 404
    assert exc.value.error_code == "group_not_found"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"error": {"code": "group_not_found"}}, "group_not_found"),
        ({"error": {"code": "not_found", "message": "нет"}}, "not_found"),
        ({"error": {"message": "no code key"}}, None),  # нет code
        ({"error": {"code": 123}}, None),  # code не строка
        ({"error": "flat"}, None),  # error не dict
        ({"d": "no error key"}, None),  # нет error
        ([1, 2, 3], None),  # тело не dict
    ],
)
def test_extract_error_code_variants(body: Any, expected: str | None) -> None:
    """Парсинг `error.code` из тела ошибки: код-строка → значение, иначе None."""
    response = httpx.Response(404, json=body)
    assert MailClient._extract_error_code(response) == expected


def test_extract_error_code_broken_body_returns_none() -> None:
    """Битое (не-JSON) / пустое тело → None (best-effort, не бросает)."""
    assert MailClient._extract_error_code(httpx.Response(404, content=b"not-json")) is None
    assert MailClient._extract_error_code(httpx.Response(404, content=b"")) is None


@pytest.mark.parametrize("status_code", [429, 500, 503])
async def test_429_and_5xx_raise_unavailable(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    """429/5xx (исчерпаны ретраи) → MailUnavailable, а не MailRejected (ADR-038)."""
    _install(monkeypatch, httpx.MockTransport(lambda _r: httpx.Response(status_code)))
    with pytest.raises(MailUnavailable):
        await _list(_client())


async def test_non_json_body_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, content=b"not-json")),
    )
    with pytest.raises(MailUnavailable):
        await _list(_client())


async def test_non_dict_json_body_raises_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, json=[1, 2, 3])),
    )
    with pytest.raises(MailUnavailable):
        await _list(_client())


async def test_generic_http_error_maps_unavailable_no_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.HTTPError("unexpected protocol error")

    _install(monkeypatch, httpx.MockTransport(handler))
    with pytest.raises(MailUnavailable):
        await _list(_client())

    assert calls["n"] == 1  # прочая ошибка httpx неретраябельна


# ------------------------------------------------------------ секрет не логируется
async def test_api_key_not_logged_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """При ошибке клиент логирует событие, но НИКОГДА не пишет ключ (05-security.md)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _install(monkeypatch, httpx.MockTransport(handler))

    # Модульный логгер mail_client мог быть закеширован ранее (cache_logger_on_first_use)
    # на устаревший список процессоров — тогда capture_logs() не перехватит событие.
    # Пересобираем свежий proxy на текущий (сброшенный autouse-фикстурой) конфиг, чтобы
    # тест был устойчив к порядку выполнения в полном наборе.
    monkeypatch.setattr(mod, "logger", structlog.get_logger(mod.__name__))

    with structlog.testing.capture_logs() as logs, pytest.raises(MailUnavailable):
        await _list(_client())

    assert logs  # событие(я) записаны
    serialized = repr(logs)
    assert KEY not in serialized
