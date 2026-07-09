"""Контрактные/интеграционные тесты роутера почты (04-api.md#mail, modules/mail, ADR-012/038).

Полный стек router→service→client, но httpx-граница к внешнему `postapp.store`
замокана `httpx.MockTransport` (реальных запросов наружу нет). JWT и `MailScope` —
через dependency_override (scope с реальной БД проверяется в `test_mail_scope_api.py`).
Проверяются: коды/схемы ответов, гейт mail_enabled (503), границы limit/пагинации (400),
проброс `order`/курсоров/фильтров (комбинируемых AND) во внешний API, маппинг внешних
кодов (404/409/422/502), write-эндпоинты ящиков и тегов, RBAC-гейты `mail:*` (403),
заголовок `Cache-Control: no-store` на write, scope-guard мутаций (403), а также
инварианты безопасности: `MAIL_API_KEY` уходит только в `X-API-Key` и НЕ присутствует в
ответах CRM; тело внешней ошибки не пробрасывается.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.api import deps
from app.domain.mail import MailScope
from conftest import make_principal
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

MAIL_KEY = "integration-mail-secret-XYZ789"
EXTERNAL_SECRET_MARKER = "EXTERNAL-TRACE-DO-NOT-LEAK"

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
_DESC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_before_id": 1001, "has_more": True}
_ASC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_since_id": 1042, "has_more": True}
_VALID_REPLY = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}
_TEAMS: dict[str, Any] = {"teams": [{"id": 3, "name": "Продажи"}]}
_MAILBOX: dict[str, Any] = {
    "id": 7,
    "email": "inbox@postapp.store",
    "display_name": "Входящие",
    "group_id": 3,
    "is_active": True,
    "last_synced_at": "2026-07-09T08:00:00Z",
    "last_sync_error": None,
    "consecutive_failures": 0,
}
_MAILBOXES: dict[str, Any] = {"mailboxes": [_MAILBOX]}
_TAG: dict[str, Any] = {
    "id": 7,
    "name": "Счета",
    "color": "#2563eb",
    "match_mode": "any",
    "is_builtin": False,
    "rules": [],
    "created_at": "2026-07-01T10:00:00Z",
    "updated_at": "2026-07-01T10:00:00Z",
}
_TAG_RULE: dict[str, Any] = {
    "id": 12,
    "type": "subject_contains",
    "pattern": "счёт",
    "created_at": "2026-07-01T10:00:00Z",
}

_ADMIN_SCOPE = MailScope(sees_all_teams=True, group_ids=frozenset())
_SCOPE_3 = MailScope(sees_all_teams=False, group_ids=frozenset({3}))

# Валидное тело create/test ящика (транзитные креды).
_CREATE_BODY: dict[str, Any] = {
    "email": "inbox@example.com",
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_ssl": True,
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_ssl": True,
    "smtp_starttls": False,
    "password": "TRANSIT-PASSWORD-SECRET",
    "group_id": 3,
}


class Recorder:
    """Записывает исходящие запросы и отдаёт заранее заданный ответ/исключение."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: Any = None,
        raise_exc: type[httpx.HTTPError] | None = None,
    ) -> None:
        self.status_code = status_code
        self.json_body = json_body
        self.raise_exc = raise_exc
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_exc is not None:
            raise self.raise_exc("boom", request=request)
        return httpx.Response(self.status_code, json=self.json_body)


def _install(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> None:
    from app.infra import mail_client as mod

    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=httpx.MockTransport(recorder.handler))

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    recorder: Recorder,
    *,
    enabled: bool = True,
    with_auth: bool = True,
    principal: Any = None,
    scope: MailScope | None = _ADMIN_SCOPE,
) -> FastAPI:
    monkeypatch.setenv("MAIL_API_KEY", MAIL_KEY if enabled else "")
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    _install(monkeypatch, recorder)
    app = create_app(get_settings())
    if with_auth:
        p = principal if principal is not None else make_principal()
        app.dependency_overrides[deps.get_current_principal] = lambda: p
    # Инъекция scope без обращения к БД (реальный резолв — в test_mail_scope_api.py).
    if scope is not None:
        app.dependency_overrides[deps.get_mail_scope] = lambda: scope
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------- гейт mail_enabled (503)
async def test_list_returns_503_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder, enabled=False)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_not_configured"
    assert recorder.requests == []


async def test_gate_precedes_limit_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder, enabled=False)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": 999})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_not_configured"


# --------------------------------------------- список desc: успех, ключ, проброс
async def test_list_desc_default_passthrough_and_api_key_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["next_since_id"] is None
    assert body["next_before_id"] == 1001
    assert body["has_more"] is True
    assert body["messages"][0]["id"] == 1042
    outgoing = recorder.requests[0]
    assert outgoing.headers.get("x-api-key") == MAIL_KEY
    assert outgoing.url.params.get("order") == "desc"
    assert outgoing.url.params.get("limit") == "20"
    assert "before_id" not in outgoing.url.params
    assert MAIL_KEY not in response.text


async def test_list_asc_with_since_id_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_ASC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/messages", params={"order": "asc", "since_id": 1000, "limit": 25}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["next_before_id"] is None
    assert body["next_since_id"] == 1042
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("order") == "asc"
    assert outgoing.url.params.get("since_id") == "1000"


# ------------------------------------- взаимоисключение режимов пагинации (400)
async def test_list_desc_with_since_id_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"order": "desc", "since_id": 100})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


@pytest.mark.parametrize("before_id", [0, -1])
async def test_list_before_id_below_one_returns_400(
    monkeypatch: pytest.MonkeyPatch, before_id: int
) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/messages", params={"order": "desc", "before_id": before_id}
        )

    assert response.status_code == 400
    assert recorder.requests == []


async def test_list_invalid_order_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"order": "sideways"})

    assert response.status_code == 400
    assert recorder.requests == []


# ------------------------------------------------------------- границы limit (400)
@pytest.mark.parametrize("limit", [0, 201])
async def test_list_limit_out_of_range_400(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": limit})

    assert response.status_code == 400
    assert recorder.requests == []


# --------------------- серверные фильтры: комбинируемы (AND), НЕ 400 (ADR-038)
async def test_list_forwards_mail_account_id_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"mail_account_id": 7})

    assert response.status_code == 200
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("mail_account_id") == "7"
    assert "group_id" not in outgoing.url.params  # group не задан (admin scope)


async def test_list_forwards_group_id_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"group_id": 3})

    assert response.status_code == 200
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("group_id") == "3"
    assert "mail_account_id" not in outgoing.url.params


async def test_list_both_filters_combined_not_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """mail_account_id + group_id вместе → НЕ 400 (взаимоисключение снято); оба уходят."""
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/messages", params={"mail_account_id": 7, "group_id": 3}
        )

    assert response.status_code == 200
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("mail_account_id") == "7"
    assert outgoing.url.params.get("group_id") == "3"


@pytest.mark.parametrize("param", ["mail_account_id", "group_id"])
async def test_list_filter_below_one_returns_400(
    monkeypatch: pytest.MonkeyPatch, param: str
) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={param: 0})

    assert response.status_code == 400
    assert recorder.requests == []


# ------------------------------------------------- маппинг внешних ошибок list
async def test_list_external_400_maps_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=400, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert EXTERNAL_SECRET_MARKER not in response.text


async def test_list_external_5xx_maps_502_and_no_body_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(status_code=500, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "mail_unavailable"
    assert EXTERNAL_SECRET_MARKER not in response.text


async def test_list_malformed_external_body_maps_502(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"messages": [], "next_before_id": None})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages")

    assert response.status_code == 502


# ------------------------------------------------------------------ reply-контракт
async def test_reply_success(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_VALID_REPLY)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post(
            "/api/mail/messages/42/reply",
            json={"to": ["sender@example.com"], "body": "Спасибо, получил."},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["sent_id"] == 5099
    outgoing = recorder.requests[0]
    assert outgoing.url.path == "/api/external/messages/42/reply"
    assert MAIL_KEY not in response.text


@pytest.mark.parametrize("body", ["", "   "])
async def test_reply_empty_body_422(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    recorder = Recorder(json_body=_VALID_REPLY)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/1/reply", json={"body": body})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"
    assert recorder.requests == []


async def test_reply_missing_body_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_VALID_REPLY)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/1/reply", json={"subject": "Re:"})

    assert response.status_code == 400
    assert recorder.requests == []


async def test_reply_external_404_maps_404_no_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=404, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/999/reply", json={"body": "x"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "mail_message_not_found"
    assert EXTERNAL_SECRET_MARKER not in response.text


# ------------------------------------------------------------------- JWT (401)
async def test_endpoints_require_jwt_401(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder, with_auth=False, scope=None)
    async with _client(app) as client:
        listed = await client.get("/api/mail/messages")
        replied = await client.post("/api/mail/messages/1/reply", json={"body": "x"})

    assert listed.status_code == 401
    assert replied.status_code == 401
    assert recorder.requests == []


# ------------------------------------------------------- GET /teams /mailboxes /tags
async def test_teams_success(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_TEAMS)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/teams")

    assert response.status_code == 200
    assert response.json()["teams"][0] == {"id": 3, "name": "Продажи"}
    assert recorder.requests[0].url.path == "/api/external/teams"


async def test_mailboxes_success_full_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_MAILBOXES)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 200
    mb = response.json()["mailboxes"][0]
    assert mb == _MAILBOX  # включая last_synced_at/last_sync_error/consecutive_failures
    outgoing = recorder.requests[0]
    assert outgoing.url.path == "/api/external/mailboxes"
    assert MAIL_KEY not in response.text


async def test_mailboxes_forwards_is_active_and_group(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_MAILBOXES)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/mailboxes", params={"is_active": "true", "group_id": 3}
        )

    assert response.status_code == 200
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("is_active") == "true"
    assert outgoing.url.params.get("group_id") == "3"


async def test_mailboxes_missing_sync_field_maps_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """Внешний DTO без sync-поля → 502 (регресс контракта, не тихое «здоров»)."""
    broken = {"mailboxes": [{k: v for k, v in _MAILBOX.items() if k != "last_synced_at"}]}
    recorder = Recorder(json_body=broken)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 502


async def test_tags_success(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"tags": [_TAG]})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/tags")

    assert response.status_code == 200
    assert response.json()["tags"][0]["id"] == 7


# ------------------------------------------------ write ящиков: контракт + no-store
async def test_create_mailbox_201_no_store_and_password_not_leaked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(status_code=201, json_body=_MAILBOX)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/mailboxes", json=_CREATE_BODY)

    assert response.status_code == 201
    assert response.headers.get("cache-control") == "no-store"
    assert response.json()["id"] == 7
    # Транзитный пароль ушёл в запрос к внешнему сервису, но НЕ в ответ CRM.
    assert b"TRANSIT-PASSWORD-SECRET" in recorder.requests[0].content
    assert "TRANSIT-PASSWORD-SECRET" not in response.text


async def test_test_mailbox_no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"imap_ok": True, "smtp_ok": True})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/mailboxes/test", json=_CREATE_BODY)

    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert response.json() == {"imap_ok": True, "smtp_ok": True}


async def test_test_mailbox_external_422_maps_422_not_502(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=422, json_body={"detail": "imap_login_failed"})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/mailboxes/test", json=_CREATE_BODY)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_create_mailbox_external_409_maps_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=409, json_body={"detail": "email_taken"})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/mailboxes", json=_CREATE_BODY)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mail_conflict"


async def test_patch_mailbox_200_no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_MAILBOX)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.patch("/api/mail/mailboxes/7", json={"is_active": False})

    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert recorder.requests[0].method == "PATCH"


async def test_delete_mailbox_204(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=204, json_body=None)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.delete("/api/mail/mailboxes/7")

    assert response.status_code == 204


async def test_sync_mailbox_202(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=202, json_body={"queued": True})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/mailboxes/7/sync")

    assert response.status_code == 202
    assert response.json() == {"queued": True}


async def test_patch_mailbox_404_maps_mailbox_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=404, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.patch("/api/mail/mailboxes/7", json={"is_active": False})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "mail_mailbox_not_found"
    assert EXTERNAL_SECRET_MARKER not in response.text


# ------------------------------------------------------------- write тегов: контракт
async def test_create_tag_201(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=201, json_body=_TAG)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/tags", json={"name": "Счета", "color": "#2563eb"})

    assert response.status_code == 201
    assert response.json()["match_mode"] == "any"


async def test_create_tag_rule_201(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=201, json_body=_TAG_RULE)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post(
            "/api/mail/tags/7/rules", json={"type": "subject_contains", "pattern": "счёт"}
        )

    assert response.status_code == 201
    assert response.json()["id"] == 12


async def test_delete_tag_builtin_409(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=409, json_body={"detail": "builtin"})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.delete("/api/mail/tags/1")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "mail_conflict"


async def test_apply_tag_to_existing_200(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"applied_count": 5})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/tags/7/apply-to-existing")

    assert response.status_code == 200
    assert response.json() == {"applied_count": 5}


# --------------------------------------------------- RBAC-гейты матрицы mail:* (403)
def _role(perms: dict[str, list[str]]) -> Any:
    return make_principal(is_superadmin=False, role="Оператор", permissions=perms)


@pytest.mark.parametrize(
    ("method", "path", "perms", "json_body"),
    [
        ("post", "/api/mail/mailboxes", {"mail": ["view"]}, _CREATE_BODY),
        ("post", "/api/mail/mailboxes/test", {"mail": ["view"]}, _CREATE_BODY),
        ("patch", "/api/mail/mailboxes/7", {"mail": ["view", "create"]}, {"is_active": False}),
        ("delete", "/api/mail/mailboxes/7", {"mail": ["view", "edit"]}, None),
        ("post", "/api/mail/mailboxes/7/sync", {"mail": ["view", "edit"]}, None),
        ("post", "/api/mail/tags", {"mail": ["view"]}, {"name": "t", "color": "#2563eb"}),
    ],
)
async def test_rbac_missing_action_is_403(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
    perms: dict[str, list[str]],
    json_body: Any,
) -> None:
    """Нет нужного действия в правах роли → 403, внешний сервис не вызывается."""
    recorder = Recorder(json_body=_MAILBOX)
    app = _build_app(monkeypatch, recorder, principal=_role(perms), scope=_ADMIN_SCOPE)
    async with _client(app) as client:
        response = await client.request(method.upper(), path, json=json_body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert recorder.requests == []


async def test_edit_without_create_still_allows_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """`mail:edit` без `mail:create` разрешает PATCH (кнопка «Проверить соединение»
    у роли без create недоступна — это UX, а PATCH гейтится edit)."""
    recorder = Recorder(json_body=_MAILBOX)
    app = _build_app(
        monkeypatch, recorder, principal=_role({"mail": ["view", "edit"]}), scope=_ADMIN_SCOPE
    )
    async with _client(app) as client:
        response = await client.patch("/api/mail/mailboxes/7", json={"is_active": False})

    assert response.status_code == 200


# ------------------------------------------------ scope-guard мутаций (403) без БД
async def test_create_mailbox_group_out_of_scope_403(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=201, json_body=_MAILBOX)
    principal = _role({"mail": ["view", "create"]})
    app = _build_app(monkeypatch, recorder, principal=principal, scope=_SCOPE_3)
    body = {**_CREATE_BODY, "group_id": 99}  # вне scope {3}
    async with _client(app) as client:
        response = await client.post("/api/mail/mailboxes", json=body)

    assert response.status_code == 403
    assert recorder.requests == []  # мутация наружу не ушла


async def test_delete_mailbox_out_of_scope_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не-админ удаляет ящик вне scope-групп (read-before-write вернул чужие id) → 403."""
    recorder = Recorder(json_body=_MAILBOXES)  # scope-guard видит только ящик id=7 группы 3
    principal = _role({"mail": ["view", "delete"]})
    app = _build_app(monkeypatch, recorder, principal=principal, scope=_SCOPE_3)
    async with _client(app) as client:
        response = await client.delete("/api/mail/mailboxes/999")

    assert response.status_code == 403
    # scope-guard дёрнул GET mailboxes, но DELETE наружу не ушёл.
    assert all(r.method == "GET" for r in recorder.requests)
