"""Unit-тесты сервиса почты `app/services/mail_service.py` (04-api.md#mail, modules/mail).

Клиент (httpx-граница к postapp.store) замокан `FakeMailClient` — реальных запросов
наружу нет. Проверяются: гейт `mail_enabled` ДО валидации `limit` (503 mail_not_configured);
диапазон `limit` 1..200 (400 validation_error); непустой `body` reply (422 unprocessable);
маппинг исключений клиента в коды CRM (404/422/502); несовместимое тело внешнего ответа →
502; проброс `next_since_id`/`has_more` 1:1 (в т.ч. `next_since_id=null`).
"""

from __future__ import annotations

from typing import Any

import pytest
from app.config import Settings
from app.errors import AppError
from app.infra.mail_client import MailMessageNotFound, MailRejected, MailUnavailable
from app.schemas.mail import MailReplyRequest
from app.services.mail_service import MailService

_VALID_LIST: dict[str, Any] = {
    "messages": [
        {
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
    ],
    "next_since_id": 1042,
    "has_more": True,
}
_VALID_REPLY: dict[str, Any] = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}


class FakeMailClient:
    """Замена MailClient: возвращает заготовку либо бросает заданное исключение."""

    def __init__(
        self,
        *,
        list_result: dict[str, Any] | None = None,
        reply_result: dict[str, Any] | None = None,
        list_exc: Exception | None = None,
        reply_exc: Exception | None = None,
    ) -> None:
        self._list_result = list_result
        self._reply_result = reply_result
        self._list_exc = list_exc
        self._reply_exc = reply_exc
        self.list_calls: list[tuple[int | None, int]] = []
        self.reply_calls: list[tuple[int, dict[str, Any]]] = []

    async def list_messages(self, since_id: int | None, limit: int) -> dict[str, Any]:
        self.list_calls.append((since_id, limit))
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


def _settings(*, mail_api_key: str) -> Settings:
    return Settings(mail_api_key=mail_api_key)


def _service(client: FakeMailClient, *, enabled: bool) -> MailService:
    return MailService(client=client, settings=_settings(mail_api_key="k" if enabled else ""))


# ------------------------------------------------------------- гейт mail_enabled
async def test_list_disabled_returns_503_not_configured() -> None:
    client = FakeMailClient(list_result=_VALID_LIST)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=False).list_messages(since_id=None, limit=50)

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


async def test_gate_precedes_limit_validation() -> None:
    """При выключенной почте невалидный limit всё равно даёт 503, а не 400."""
    client = FakeMailClient(list_result=_VALID_LIST)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=False).list_messages(since_id=None, limit=0)

    assert exc.value.status_code == 503
    assert exc.value.code == "mail_not_configured"


# ------------------------------------------------------------------ валидация limit
@pytest.mark.parametrize("limit", [0, -5, 201, 1000])
async def test_list_limit_out_of_range_returns_400(limit: int) -> None:
    client = FakeMailClient(list_result=_VALID_LIST)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_messages(since_id=None, limit=limit)

    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"
    assert client.list_calls == []


@pytest.mark.parametrize("limit", [1, 50, 200])
async def test_list_limit_boundaries_ok(limit: int) -> None:
    client = FakeMailClient(list_result=_VALID_LIST)
    result = await _service(client, enabled=True).list_messages(since_id=10, limit=limit)

    assert result.next_since_id == 1042
    assert client.list_calls == [(10, limit)]


# ------------------------------------------------------------- проброс пагинации
async def test_list_passes_through_next_since_id_and_has_more() -> None:
    client = FakeMailClient(list_result=_VALID_LIST)
    result = await _service(client, enabled=True).list_messages(since_id=None, limit=50)

    assert result.next_since_id == 1042
    assert result.has_more is True
    assert len(result.messages) == 1
    assert result.messages[0].id == 1042


async def test_list_null_next_since_id_empty_batch_is_valid() -> None:
    client = FakeMailClient(list_result={"messages": [], "next_since_id": None, "has_more": False})
    result = await _service(client, enabled=True).list_messages(since_id=5000, limit=50)

    assert result.messages == []
    assert result.next_since_id is None
    assert result.has_more is False


async def test_list_incompatible_body_maps_to_502() -> None:
    # Отсутствует обязательное поле has_more → схема не проходит → 502.
    client = FakeMailClient(list_result={"messages": [], "next_since_id": None})
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_messages(since_id=None, limit=50)

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


# --------------------------------------------------------------- маппинг ошибок list
async def test_list_unavailable_maps_to_502() -> None:
    client = FakeMailClient(list_exc=MailUnavailable("down"))
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_messages(since_id=None, limit=50)

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


@pytest.mark.parametrize("client_exc", [MailMessageNotFound("404"), MailRejected("400")])
async def test_list_unexpected_client_error_maps_to_502(client_exc: Exception) -> None:
    client = FakeMailClient(list_exc=client_exc)
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=True).list_messages(since_id=None, limit=50)

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
    client = FakeMailClient(reply_exc=MailRejected("400"))
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
