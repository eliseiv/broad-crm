"""Контрактные/интеграционные тесты роутера почты (04-api.md#mail, modules/mail).

Полный стек router→service→client, но httpx-граница к внешнему `postapp.store`
замокана `httpx.MockTransport` (реальных запросов наружу нет). JWT — через
dependency_override. Проверяются коды/схемы ответов, гейт mail_enabled (503) ДО
валидации limit, границы limit (400), валидация reply (422/400), маппинг внешних
кодов (404/422/502), проброс пагинации (next_since_id/has_more, null-курсор), а также
инварианты безопасности: `MAIL_API_KEY` уходит только в заголовок `X-API-Key`
исходящего запроса и НЕ присутствует в ответах CRM; тело внешней ошибки не пробрасывается.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.api import deps
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
_VALID_LIST = {"messages": [_MESSAGE], "next_since_id": 1042, "has_more": True}
_VALID_REPLY = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}


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
        app.dependency_overrides[deps.get_current_user] = lambda: "admin"
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------- гейт mail_enabled (503)
async def test_list_returns_503_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_VALID_LIST)
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
    recorder = Recorder(json_body=_VALID_LIST)
    app = _build_app(monkeypatch, recorder, enabled=False)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": 999})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mail_not_configured"


# --------------------------------------------------- список: успех, ключ, проброс
async def test_list_success_passthrough_and_api_key_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder(json_body=_VALID_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"since_id": 100, "limit": 25})

    assert response.status_code == 200
    body = response.json()
    assert body["next_since_id"] == 1042
    assert body["has_more"] is True
    assert body["messages"][0]["id"] == 1042
    # Ключ ушёл только в заголовок исходящего запроса; в ответе CRM его нет.
    outgoing = recorder.requests[0]
    assert outgoing.headers.get("x-api-key") == MAIL_KEY
    assert outgoing.url.params.get("since_id") == "100"
    assert outgoing.url.params.get("limit") == "25"
    assert MAIL_KEY not in response.text


async def test_list_null_next_since_id_empty_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body={"messages": [], "next_since_id": None, "has_more": False})
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["messages"] == []
    assert body["next_since_id"] is None
    assert body["has_more"] is False


@pytest.mark.parametrize("limit", [0, 201])
async def test_list_limit_out_of_range_400(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    recorder = Recorder(json_body=_VALID_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": limit})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


async def test_list_invalid_limit_type_400(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(json_body=_VALID_LIST)
    app = _build_app(monkeypatch, recorder)
    async with _client(app) as client:
        response = await client.get("/api/mail/messages", params={"limit": "abc"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert recorder.requests == []


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
    recorder = Recorder(json_body={"messages": [], "next_since_id": None})
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
    recorder = Recorder(json_body=_VALID_LIST)
    app = _build_app(monkeypatch, recorder, with_auth=False)
    async with _client(app) as client:
        listed = await client.get("/api/mail/messages")
        replied = await client.post("/api/mail/messages/1/reply", json={"body": "x"})

    assert listed.status_code == 401
    assert listed.json()["error"]["code"] == "unauthorized"
    assert replied.status_code == 401
    assert replied.json()["error"]["code"] == "unauthorized"
    assert recorder.requests == []  # без JWT внешний сервис не вызывается
