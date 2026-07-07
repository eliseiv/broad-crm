"""Контрактные/интеграционные тесты роутера почты (04-api.md#mail, modules/mail, ADR-013).

Полный стек router→service→client, но httpx-граница к внешнему `postapp.store`
замокана `httpx.MockTransport` (реальных запросов наружу нет). JWT — через
dependency_override. Проверяются коды/схемы ответов, гейт mail_enabled (503) ДО
валидации limit, границы limit (400), проброс `order`/курсоров во внешний API,
взаимоисключение режимов пагинации (400), `before_id` `ge=1` (400), нормализация
курсоров (незапрошенный → null), валидация reply (422/400), маппинг внешних кодов
(404/422/502, внешний 400 list → 400), а также инварианты безопасности: `MAIL_API_KEY`
уходит только в заголовок `X-API-Key` исходящего запроса и НЕ присутствует в ответах CRM;
тело внешней ошибки не пробрасывается.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.api import deps
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
# Внешний ответ desc-режима: несёт `next_before_id` (основной режим страницы «Почты»).
_DESC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_before_id": 1001, "has_more": True}
# Внешний ответ asc-режима: несёт `next_since_id` (обратная совместимость).
_ASC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_since_id": 1042, "has_more": True}
_VALID_REPLY = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}
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
) -> FastAPI:
    monkeypatch.setenv("MAIL_API_KEY", MAIL_KEY if enabled else "")
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    _install(monkeypatch, recorder)
    app = create_app(get_settings())
    if with_auth:
        app.dependency_overrides[deps.get_current_principal] = lambda: make_principal()
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
    assert recorder.requests == []  # внешний сервис не вызывался


async def test_reply_returns_503_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_VALID_REPLY)
    app = _build_app(monkeypatch, recorder, enabled=False)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/1/reply", json={"body": "ответ"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_not_configured"
    assert recorder.requests == []


async def test_gate_precedes_limit_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выключенная почта + невалидный limit → 503 (гейт до валидации диапазона)."""
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
    """order=desc (default) без before_id → внешний вызов order=desc; ответ: next_since_id
    null, next_before_id из внешнего API. Ключ — только в заголовке, не в ответе."""
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
    assert outgoing.url.params.get("order") == "desc"  # order передаётся всегда явно
    assert outgoing.url.params.get("limit") == "20"
    assert "before_id" not in outgoing.url.params
    assert "since_id" not in outgoing.url.params
    assert MAIL_KEY not in response.text


async def test_list_desc_with_before_id_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """order=desc&before_id=N → проброс before_id; next_since_id принудительно null."""
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/messages", params={"order": "desc", "before_id": 1001, "limit": 20}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["next_since_id"] is None
    assert body["next_before_id"] == 1001
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("order") == "desc"
    assert outgoing.url.params.get("before_id") == "1001"
    assert "since_id" not in outgoing.url.params


async def test_list_asc_with_since_id_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """order=asc&since_id=N → BC: проброс since_id; next_before_id принудительно null."""
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
    assert "before_id" not in outgoing.url.params


async def test_list_desc_null_next_before_id_empty_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"messages": [], "next_before_id": None, "has_more": False})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"before_id": 500})

    assert response.status_code == 200
    body = response.json()
    assert body["messages"] == []
    assert body["next_before_id"] is None
    assert body["next_since_id"] is None
    assert body["has_more"] is False


# ------------------------------------- взаимоисключение режимов пагинации (400)
async def test_list_desc_with_since_id_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"order": "desc", "since_id": 100})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []  # локальная валидация — до внешнего вызова


async def test_list_asc_with_before_id_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_ASC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/messages", params={"order": "asc", "before_id": 1001}
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


@pytest.mark.parametrize("before_id", [0, -1])
async def test_list_before_id_below_one_returns_400(
    monkeypatch: pytest.MonkeyPatch, before_id: int
) -> None:
    """before_id < 1 → 400 validation_error (Query ge=1), внешний сервис не вызывается."""
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/messages", params={"order": "desc", "before_id": before_id}
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


async def test_list_invalid_order_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"order": "sideways"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


# ------------------------------------------------------------- границы limit (400)
@pytest.mark.parametrize("limit", [0, 201])
async def test_list_limit_out_of_range_400(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": limit})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


async def test_list_invalid_limit_type_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": "abc"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


# ------------------------------------------------- маппинг внешних ошибок list
async def test_list_external_400_maps_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Внешний 400 (рассинхрон взаимоисключения) → 400 validation_error (04-api.md#mail)."""
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
    # Тело внешней ошибки не пробрасывается в ответ CRM.
    assert EXTERNAL_SECRET_MARKER not in response.text


async def test_list_malformed_external_body_maps_502(monkeypatch: pytest.MonkeyPatch) -> None:
    # Нет обязательного has_more → схема не проходит → 502.
    recorder = Recorder(json_body={"messages": [], "next_before_id": None})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "mail_unavailable"


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
    assert body["smtp_message_id"] == "<abc123@postapp.store>"
    outgoing = recorder.requests[0]
    assert outgoing.headers.get("x-api-key") == MAIL_KEY
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
    assert recorder.requests == []  # некорректное тело наружу не уходит


async def test_reply_missing_body_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_VALID_REPLY)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/1/reply", json={"subject": "Re:"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


async def test_reply_external_404_maps_404_no_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=404, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/999/reply", json={"body": "x"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "mail_message_not_found"
    assert EXTERNAL_SECRET_MARKER not in response.text


@pytest.mark.parametrize("status_code", [400, 422])
async def test_reply_external_other_4xx_maps_422(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    recorder = Recorder(status_code=status_code, json_body={"detail": "bad"})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/1/reply", json={"body": "x"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unprocessable"


async def test_reply_external_5xx_maps_502_no_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=503, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.post("/api/mail/messages/1/reply", json={"body": "x"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "mail_unavailable"
    assert EXTERNAL_SECRET_MARKER not in response.text


# ------------------------------------------------------------------- JWT (401)
async def test_endpoints_require_jwt_401(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder, with_auth=False)
    async with _client(app) as client:
        listed = await client.get("/api/mail/messages")
        replied = await client.post("/api/mail/messages/1/reply", json={"body": "x"})

    assert listed.status_code == 401
    assert listed.json()["error"]["code"] == "unauthorized"
    assert replied.status_code == 401
    assert replied.json()["error"]["code"] == "unauthorized"
    assert recorder.requests == []  # без JWT внешний сервис не вызывается


# ---------------------- серверные фильтры messages по ящику/команде (ADR-017)
async def test_list_forwards_mail_account_id_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"mail_account_id": 7})

    assert response.status_code == 200
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("mail_account_id") == "7"
    assert "group_id" not in outgoing.url.params  # взаимоисключающе


async def test_list_forwards_group_id_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"group_id": 3})

    assert response.status_code == 200
    outgoing = recorder.requests[0]
    assert outgoing.url.params.get("group_id") == "3"
    assert "mail_account_id" not in outgoing.url.params


async def test_list_both_filters_returns_400_field_filter_no_external(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mail_account_id И group_id → 400 validation_error (field=filter) ЛОКАЛЬНО,
    внешний сервис не вызывается (04-api.md#mail, ADR-017)."""
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get(
            "/api/mail/messages", params={"mail_account_id": 7, "group_id": 3}
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"][0]["field"] == "filter"
    assert recorder.requests == []  # локальная валидация — до внешнего вызова


async def test_list_external_400_filter_maps_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Внешний 400 (рассинхрон взаимоисключения фильтров) → 400 validation_error."""
    recorder = Recorder(status_code=400, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"mail_account_id": 7})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert EXTERNAL_SECRET_MARKER not in response.text


async def test_list_unknown_filter_id_returns_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Несуществующий/чужой/non-canonical id → внешний сервис отдаёт пустую страницу
    (200, не 404); CRM проксирует как обычный 200 (04-api.md#mail, ADR-017)."""
    recorder = Recorder(json_body={"messages": [], "next_before_id": None, "has_more": False})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"mail_account_id": 999999})

    assert response.status_code == 200
    body = response.json()
    assert body["messages"] == []
    assert body["has_more"] is False
    assert recorder.requests[0].url.params.get("mail_account_id") == "999999"


@pytest.mark.parametrize("param", ["mail_account_id", "group_id"])
async def test_list_filter_below_one_returns_400(
    monkeypatch: pytest.MonkeyPatch, param: str
) -> None:
    """mail_account_id/group_id < 1 → 400 validation_error (Query ge=1), без внешнего вызова."""
    recorder = Recorder(json_body=_DESC_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={param: 0})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


# ------------------------------------------------------- GET /api/mail/teams (ADR-017)
async def test_teams_success_and_api_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_TEAMS)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/teams")

    assert response.status_code == 200
    body = response.json()
    assert body["teams"][0] == {"id": 3, "name": "Продажи"}
    outgoing = recorder.requests[0]
    assert outgoing.url.path == "/api/external/teams"
    assert outgoing.headers.get("x-api-key") == MAIL_KEY
    assert MAIL_KEY not in response.text


async def test_teams_empty_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"teams": []})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/teams")

    assert response.status_code == 200
    assert response.json()["teams"] == []


async def test_teams_returns_503_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_TEAMS)
    app = _build_app(monkeypatch, recorder, enabled=False)
    async with _client(app) as client:
        response = await client.get("/api/mail/teams")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_not_configured"
    assert recorder.requests == []


async def test_teams_external_5xx_maps_502_no_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=500, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/teams")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "mail_unavailable"
    assert EXTERNAL_SECRET_MARKER not in response.text


async def test_teams_requires_jwt_401(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_TEAMS)
    app = _build_app(monkeypatch, recorder, with_auth=False)
    async with _client(app) as client:
        response = await client.get("/api/mail/teams")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert recorder.requests == []


# -------------------------------------------------- GET /api/mail/mailboxes (ADR-017)
async def test_mailboxes_success_full_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_MAILBOXES)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 200
    mb = response.json()["mailboxes"][0]
    assert mb == {
        "id": 7,
        "email": "inbox@postapp.store",
        "display_name": "Входящие",
        "group_id": 3,
        "is_active": True,
    }
    outgoing = recorder.requests[0]
    assert outgoing.url.path == "/api/external/mailboxes"
    assert outgoing.headers.get("x-api-key") == MAIL_KEY
    assert MAIL_KEY not in response.text


async def test_mailboxes_nullable_fields_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(
        json_body={
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
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 200
    mb = response.json()["mailboxes"][0]
    assert mb["display_name"] is None
    assert mb["group_id"] is None
    assert mb["is_active"] is False


async def test_mailboxes_empty_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"mailboxes": []})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 200
    assert response.json()["mailboxes"] == []


async def test_mailboxes_returns_503_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_MAILBOXES)
    app = _build_app(monkeypatch, recorder, enabled=False)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_not_configured"
    assert recorder.requests == []


async def test_mailboxes_external_5xx_maps_502_no_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(status_code=503, json_body={"detail": EXTERNAL_SECRET_MARKER})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "mail_unavailable"
    assert EXTERNAL_SECRET_MARKER not in response.text


async def test_mailboxes_requires_jwt_401(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_MAILBOXES)
    app = _build_app(monkeypatch, recorder, with_auth=False)
    async with _client(app) as client:
        response = await client.get("/api/mail/mailboxes")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert recorder.requests == []
