"""Unit-тесты сервиса почты `app/services/mail_service.py` (04-api.md#mail, ADR-012/038).

Клиент (httpx-граница к postapp.store) замокан `FakeMailClient` — реальных запросов
наружу нет. Проверяются: гейт `mail_enabled` ДО валидации `limit` (503 mail_not_configured);
диапазон `limit` 1..200 (400 validation_error); взаимоисключение режимов пагинации
(`since_id` при desc / `before_id` при asc → 400 ДО внешнего вызова); фильтры
`mail_account_id`/`group_id` **AND-комбинируемы** (оба уходят, НЕ 400 — ADR-038);
нормализация курсоров; **`MailScope`** — граница безопасности (анти-энумерация чтения без
вызова внешнего API у не-админа вне scope; мутация вне scope → 403); постатусный маппинг
исключений клиента write в коды CRM (400→validation_error, 404→контекст, 409→mail_conflict,
422→unprocessable; отсутствие sync-поля во внешнем DTO → 502); справочники teams/mailboxes/
tags; непустой `body` reply (422); write-CRUD ящиков и тегов.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.config import Settings
from app.domain.mail import MailScope
from app.errors import AppError
from app.infra.mail_client import MailRejected, MailUnavailable
from app.schemas.mail import (
    MailMailboxCreateRequest,
    MailMailboxTestRequest,
    MailMailboxUpdateRequest,
    MailOrder,
    MailReplyRequest,
    MailTagCreateRequest,
    MailTagRuleCreateRequest,
    MailTagUpdateRequest,
)
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
_DESC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_before_id": 1001, "has_more": True}
_ASC_LIST: dict[str, Any] = {"messages": [_MESSAGE], "next_since_id": 1042, "has_more": True}
_VALID_REPLY: dict[str, Any] = {"sent_id": 5099, "smtp_message_id": "<abc123@postapp.store>"}
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

# Scope admin-уровня (видит всё) и scope не-админа с группами {3}.
_ADMIN = MailScope(sees_all_teams=True, group_ids=frozenset())
_SCOPE_3 = MailScope(sees_all_teams=False, group_ids=frozenset({3}))
_EMPTY_SCOPE = MailScope(sees_all_teams=False, group_ids=frozenset())


def _create_payload(**over: Any) -> MailMailboxCreateRequest:
    base: dict[str, Any] = {
        "email": "inbox@example.com",
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "imap_ssl": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "smtp_starttls": False,
        "password": "secret",
    }
    base.update(over)
    return MailMailboxCreateRequest(**base)


def _test_payload() -> MailMailboxTestRequest:
    return MailMailboxTestRequest(
        email="inbox@example.com",
        imap_host="imap.example.com",
        imap_port=993,
        imap_ssl=True,
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_ssl=True,
        smtp_starttls=False,
        password="secret",
    )


class FakeMailClient:
    """Замена MailClient: программируемые результаты/исключения по методам + запись вызовов.

    Сигнатуры повторяют реальный клиент (все аргументы keyword-only, `group_ids` —
    повторяемый; `mail_account_id`+`group_ids` комбинируемы). Вызовы фиксируются, чтобы
    проверить проброс фильтров/курсоров и что при локальной ошибке (валидация/пустой
    scope) внешний сервис не вызывается.
    """

    def __init__(self, **kw: Any) -> None:
        self._results: dict[str, Any] = kw.get("results", {})
        self._excs: dict[str, Exception | None] = kw.get("excs", {})
        self.list_calls: list[dict[str, Any]] = []
        self.mailbox_list_calls: list[dict[str, Any]] = []
        self.reply_calls: list[tuple[int, dict[str, Any]]] = []
        self.write_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.teams_calls = 0
        self.tags_calls = 0

    def _return(self, name: str, default: Any = None) -> Any:
        if self._excs.get(name) is not None:
            raise self._excs[name]  # type: ignore[misc]
        return self._results.get(name, default)

    async def list_messages(
        self,
        *,
        order: str,
        since_id: int | None,
        before_id: int | None,
        limit: int,
        mail_account_id: int | None,
        group_ids: Any = None,
    ) -> dict[str, Any]:
        self.list_calls.append(
            {
                "order": order,
                "since_id": since_id,
                "before_id": before_id,
                "limit": limit,
                "mail_account_id": mail_account_id,
                "group_ids": list(group_ids) if group_ids is not None else None,
            }
        )
        return self._return("list_messages", _DESC_LIST)

    async def list_teams(self) -> dict[str, Any]:
        self.teams_calls += 1
        return self._return("list_teams", _TEAMS)

    async def list_mailboxes(
        self, *, is_active: bool | None = None, group_ids: Any = None
    ) -> dict[str, Any]:
        self.mailbox_list_calls.append(
            {
                "is_active": is_active,
                "group_ids": list(group_ids) if group_ids is not None else None,
            }
        )
        return self._return("list_mailboxes", _MAILBOXES)

    async def list_tags(self) -> dict[str, Any]:
        self.tags_calls += 1
        return self._return("list_tags", {"tags": [_TAG]})

    async def reply(self, message_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.reply_calls.append((message_id, payload))
        return self._return("reply", _VALID_REPLY)

    async def test_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_calls.append(("test_mailbox", (payload,)))
        return self._return("test_mailbox", {"imap_ok": True, "smtp_ok": True})

    async def create_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_calls.append(("create_mailbox", (payload,)))
        return self._return("create_mailbox", _MAILBOX)

    async def update_mailbox(self, mailbox_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_calls.append(("update_mailbox", (mailbox_id, payload)))
        return self._return("update_mailbox", _MAILBOX)

    async def delete_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        self.write_calls.append(("delete_mailbox", (mailbox_id,)))
        return self._return("delete_mailbox", {})

    async def sync_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        self.write_calls.append(("sync_mailbox", (mailbox_id,)))
        return self._return("sync_mailbox", {"queued": True})

    async def create_tag(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_calls.append(("create_tag", (payload,)))
        return self._return("create_tag", _TAG)

    async def update_tag(self, tag_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_calls.append(("update_tag", (tag_id, payload)))
        return self._return("update_tag", _TAG)

    async def delete_tag(self, tag_id: int) -> dict[str, Any]:
        self.write_calls.append(("delete_tag", (tag_id,)))
        return self._return("delete_tag", {})

    async def create_tag_rule(self, tag_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        self.write_calls.append(("create_tag_rule", (tag_id, payload)))
        return self._return("create_tag_rule", _TAG_RULE)

    async def delete_tag_rule(self, tag_id: int, rule_id: int) -> dict[str, Any]:
        self.write_calls.append(("delete_tag_rule", (tag_id, rule_id)))
        return self._return("delete_tag_rule", {})

    async def apply_tag_to_existing(self, tag_id: int) -> dict[str, Any]:
        self.write_calls.append(("apply_tag_to_existing", (tag_id,)))
        return self._return("apply_tag_to_existing", {"applied_count": 5})


def _settings(*, mail_api_key: str) -> Settings:
    return Settings(mail_api_key=mail_api_key)


def _service(client: FakeMailClient, *, enabled: bool = True) -> MailService:
    return MailService(client=client, settings=_settings(mail_api_key="k" if enabled else ""))


async def _list(
    service: MailService,
    *,
    scope: MailScope = _ADMIN,
    order: MailOrder = "desc",
    since_id: int | None = None,
    before_id: int | None = None,
    limit: int = 50,
    mail_account_id: int | None = None,
    group_id: int | None = None,
) -> Any:
    return await service.list_messages(
        scope=scope,
        order=order,
        since_id=since_id,
        before_id=before_id,
        limit=limit,
        mail_account_id=mail_account_id,
        group_id=group_id,
    )


# ------------------------------------------------------------- гейт mail_enabled
async def test_list_disabled_returns_503_not_configured() -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=False))

    assert exc.value.status_code == 503
    assert exc.value.code == "mail_not_configured"
    assert client.list_calls == []


async def test_gate_precedes_limit_validation() -> None:
    """Выключенная почта + невалидный limit → 503, а не 400."""
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _list(_service(client, enabled=False), limit=0)

    assert exc.value.code == "mail_not_configured"


@pytest.mark.parametrize(
    "method",
    ["list_teams", "list_tags"],
)
async def test_reference_disabled_returns_503(method: str) -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await getattr(_service(client, enabled=False), method)()

    assert exc.value.code == "mail_not_configured"


async def test_mailboxes_disabled_returns_503() -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _service(client, enabled=False).list_mailboxes(
            scope=_ADMIN, is_active=None, group_id=None
        )

    assert exc.value.code == "mail_not_configured"
    assert client.mailbox_list_calls == []


# ------------------------------------------------------------------ валидация limit
@pytest.mark.parametrize("limit", [0, -5, 201, 1000])
async def test_list_limit_out_of_range_returns_400(limit: int) -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _list(_service(client), limit=limit)

    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"
    assert client.list_calls == []


@pytest.mark.parametrize("limit", [1, 50, 200])
async def test_list_limit_boundaries_ok(limit: int) -> None:
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    result = await _list(_service(client), limit=limit)

    assert result.next_before_id == 1001
    assert client.list_calls[0]["limit"] == limit


# ----------------------------------------------------- desc/asc-режимы, курсоры
async def test_list_desc_default_forwards_order_and_nulls_since_cursor() -> None:
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    result = await _list(_service(client))

    assert client.list_calls[0]["order"] == "desc"
    assert result.next_since_id is None
    assert result.next_before_id == 1001
    assert result.has_more is True
    assert result.messages[0].id == 1042


async def test_list_asc_with_since_id_forces_before_null() -> None:
    client = FakeMailClient(results={"list_messages": _ASC_LIST})
    result = await _list(_service(client), order="asc", since_id=1000)

    assert client.list_calls[0]["since_id"] == 1000
    assert result.next_before_id is None
    assert result.next_since_id == 1042


async def test_list_desc_normalizes_stray_since_cursor_from_external() -> None:
    client = FakeMailClient(
        results={
            "list_messages": {
                "messages": [_MESSAGE],
                "next_since_id": 9999,
                "next_before_id": 1001,
                "has_more": True,
            }
        }
    )
    result = await _list(_service(client))

    assert result.next_since_id is None  # незапрошенный курсор обнулён
    assert result.next_before_id == 1001


# ------------------------------------------------- взаимоисключение режимов пагинации
async def test_list_desc_with_since_id_returns_400_before_external() -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _list(_service(client), order="desc", since_id=1000)

    assert exc.value.code == "validation_error"
    assert exc.value.details[0]["field"] == "since_id"
    assert client.list_calls == []


async def test_list_asc_with_before_id_returns_400_before_external() -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _list(_service(client), order="asc", before_id=1001)

    assert exc.value.code == "validation_error"
    assert exc.value.details[0]["field"] == "before_id"
    assert client.list_calls == []


# ---------------------------------- фильтры комбинируемы (AND), взаимоисключения нет
async def test_list_both_filters_combined_not_400() -> None:
    """mail_account_id + group_id вместе → НЕ 400 (взаимоисключение снято, ADR-038);
    admin: оба уходят во внешний API (group_ids=[group_id])."""
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    await _list(_service(client), scope=_ADMIN, mail_account_id=7, group_id=3)

    call = client.list_calls[0]
    assert call["mail_account_id"] == 7
    assert call["group_ids"] == [3]


async def test_list_admin_forwards_group_id_as_single() -> None:
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    await _list(_service(client), scope=_ADMIN, group_id=3)

    assert client.list_calls[0]["group_ids"] == [3]


async def test_list_admin_no_group_passes_none() -> None:
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    await _list(_service(client), scope=_ADMIN)

    assert client.list_calls[0]["group_ids"] is None


# --------------------------------------------- MailScope: анти-энумерация чтения ------
async def test_list_non_admin_empty_scope_returns_empty_without_external_call() -> None:
    """Ключевой инвариант (ADR-038 §3): не-админ с пустым group_ids → пустая страница
    БЕЗ вызова внешнего API (мок не должен быть дёрнут)."""
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    result = await _list(_service(client), scope=_EMPTY_SCOPE)

    assert result.messages == []
    assert result.has_more is False
    assert result.next_before_id is None
    assert result.next_since_id is None
    assert client.list_calls == []  # внешний сервис не вызван вообще


async def test_list_non_admin_no_filter_injects_scope_groups() -> None:
    """Не-админ без фильтра «Команда» → сервис инъектирует group_ids = scope.group_ids."""
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    scope = MailScope(sees_all_teams=False, group_ids=frozenset({3, 8}))
    await _list(_service(client), scope=scope)

    assert client.list_calls[0]["group_ids"] == [3, 8]  # отсортировано


async def test_list_non_admin_group_in_scope_narrows_to_it() -> None:
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    scope = MailScope(sees_all_teams=False, group_ids=frozenset({3, 8}))
    await _list(_service(client), scope=scope, group_id=3)

    assert client.list_calls[0]["group_ids"] == [3]


async def test_list_non_admin_group_out_of_scope_returns_empty_no_call() -> None:
    """Не-админ фильтрует по группе вне scope → пусто (не 403/404), без внешнего вызова."""
    client = FakeMailClient(results={"list_messages": _DESC_LIST})
    result = await _list(_service(client), scope=_SCOPE_3, group_id=999)

    assert result.messages == []
    assert client.list_calls == []


async def test_list_non_admin_foreign_mail_account_id_and_intersection_empty() -> None:
    """Не-админ с чужим mail_account_id: сервис всё равно инъектирует scope-группы;
    внешний AND даёт пустое пересечение (эмулируем пустой ответ внешнего)."""
    client = FakeMailClient(
        results={"list_messages": {"messages": [], "next_before_id": None, "has_more": False}}
    )
    result = await _list(_service(client), scope=_SCOPE_3, mail_account_id=999)

    call = client.list_calls[0]
    assert call["mail_account_id"] == 999
    assert call["group_ids"] == [3]  # scope-группа инъектирована вместе с чужим ящиком
    assert result.messages == []


# --------------------------------------------------------------- маппинг ошибок list
async def test_list_unavailable_maps_to_502() -> None:
    client = FakeMailClient(excs={"list_messages": MailUnavailable("down")})
    with pytest.raises(AppError) as exc:
        await _list(_service(client))

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


async def test_list_external_400_maps_to_400_validation_error() -> None:
    client = FakeMailClient(excs={"list_messages": MailRejected(400)})
    with pytest.raises(AppError) as exc:
        await _list(_service(client))

    assert exc.value.code == "validation_error"


@pytest.mark.parametrize("client_exc", [MailRejected(404), MailRejected(409), MailRejected(422)])
async def test_list_other_client_error_maps_to_502(client_exc: Exception) -> None:
    client = FakeMailClient(excs={"list_messages": client_exc})
    with pytest.raises(AppError) as exc:
        await _list(_service(client))

    assert exc.value.code == "mail_unavailable"


async def test_list_incompatible_body_maps_to_502() -> None:
    client = FakeMailClient(results={"list_messages": {"messages": [], "next_before_id": None}})
    with pytest.raises(AppError) as exc:
        await _list(_service(client))

    assert exc.value.code == "mail_unavailable"


# --------------------------------------------------------------- teams / tags -------
async def test_list_teams_success_returns_schema() -> None:
    client = FakeMailClient(results={"list_teams": _TEAMS})
    result = await _service(client).list_teams()

    assert result.teams[0].id == 3
    assert result.teams[0].name == "Продажи"


@pytest.mark.parametrize(
    "client_exc", [MailUnavailable("down"), MailRejected(400), MailRejected(404)]
)
async def test_list_teams_any_client_error_maps_to_502(client_exc: Exception) -> None:
    client = FakeMailClient(excs={"list_teams": client_exc})
    with pytest.raises(AppError) as exc:
        await _service(client).list_teams()

    assert exc.value.code == "mail_unavailable"


async def test_list_tags_success_returns_schema() -> None:
    client = FakeMailClient(results={"list_tags": {"tags": [_TAG]}})
    result = await _service(client).list_tags()

    assert result.tags[0].id == 7
    assert result.tags[0].name == "Счета"


# ------------------------------------------------- mailboxes list + scope -----------
async def test_mailboxes_admin_forwards_filters() -> None:
    client = FakeMailClient(results={"list_mailboxes": _MAILBOXES})
    result = await _service(client).list_mailboxes(scope=_ADMIN, is_active=True, group_id=3)

    assert client.mailbox_list_calls[0] == {"is_active": True, "group_ids": [3]}
    assert result.mailboxes[0].id == 7
    assert result.mailboxes[0].consecutive_failures == 0


async def test_mailboxes_non_admin_empty_scope_empty_no_call() -> None:
    client = FakeMailClient(results={"list_mailboxes": _MAILBOXES})
    result = await _service(client).list_mailboxes(
        scope=_EMPTY_SCOPE, is_active=None, group_id=None
    )

    assert result.mailboxes == []
    assert client.mailbox_list_calls == []


async def test_mailboxes_missing_sync_field_maps_502() -> None:
    """Отсутствие обязательного sync-поля во внешнем DTO → 502 (регресс контракта),
    НЕ тихое «ящик здоров» (ADR-038; MailMailbox — required sync-поля)."""
    broken = {"mailboxes": [{k: v for k, v in _MAILBOX.items() if k != "consecutive_failures"}]}
    client = FakeMailClient(results={"list_mailboxes": broken})
    with pytest.raises(AppError) as exc:
        await _service(client).list_mailboxes(scope=_ADMIN, is_active=None, group_id=None)

    assert exc.value.status_code == 502
    assert exc.value.code == "mail_unavailable"


# ----------------------------------------------------------------- reply ------------
@pytest.mark.parametrize("body", ["", "   ", "\n\t "])
async def test_reply_empty_or_whitespace_body_returns_422(body: str) -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _service(client).reply(message_id=1, payload=MailReplyRequest(body=body))

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"
    assert client.reply_calls == []


async def test_reply_success_excludes_none_fields() -> None:
    client = FakeMailClient(results={"reply": _VALID_REPLY})
    result = await _service(client).reply(
        message_id=42, payload=MailReplyRequest(body="Спасибо, получил.")
    )

    assert result.sent_id == 5099
    sent_payload = client.reply_calls[0][1]
    assert sent_payload == {"body": "Спасибо, получил."}


async def test_reply_not_found_maps_to_404() -> None:
    client = FakeMailClient(excs={"reply": MailRejected(404)})
    with pytest.raises(AppError) as exc:
        await _service(client).reply(message_id=1, payload=MailReplyRequest(body="x"))

    assert exc.value.status_code == 404
    assert exc.value.code == "mail_message_not_found"


async def test_reply_other_4xx_maps_to_422() -> None:
    client = FakeMailClient(excs={"reply": MailRejected(400)})
    with pytest.raises(AppError) as exc:
        await _service(client).reply(message_id=1, payload=MailReplyRequest(body="x"))

    assert exc.value.status_code == 422


# ------------------------------------------------------ write ящиков: постатус маппинг
async def test_create_mailbox_success_returns_schema() -> None:
    client = FakeMailClient(results={"create_mailbox": _MAILBOX})
    result = await _service(client).create_mailbox(
        scope=_ADMIN, payload=_create_payload(group_id=3)
    )

    assert result.id == 7
    assert client.write_calls[0][0] == "create_mailbox"


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "validation_error"),
        (404, "mail_mailbox_not_found"),  # плоский 404 без error_code → контекст ящика
        (409, "mail_conflict"),  # email уже заведён (НЕ 422 — был баг)
        (422, "unprocessable"),
    ],
)
async def test_create_mailbox_rejected_mapping(status_code: int, expected_code: str) -> None:
    client = FakeMailClient(excs={"create_mailbox": MailRejected(status_code)})
    with pytest.raises(AppError) as exc:
        await _service(client).create_mailbox(scope=_ADMIN, payload=_create_payload(group_id=3))

    assert exc.value.code == expected_code


async def test_create_mailbox_404_group_not_found_maps_group_not_found() -> None:
    """404 c внешним `error.code=group_not_found` при создании → mail_group_not_found."""
    client = FakeMailClient(excs={"create_mailbox": MailRejected(404, "group_not_found")})
    with pytest.raises(AppError) as exc:
        await _service(client).create_mailbox(scope=_ADMIN, payload=_create_payload(group_id=3))

    assert exc.value.status_code == 404
    assert exc.value.code == "mail_group_not_found"


async def test_update_mailbox_404_group_not_found_maps_group_not_found() -> None:
    """404 c внешним `error.code=group_not_found` при правке (transfer) → mail_group_not_found."""
    client = FakeMailClient(excs={"update_mailbox": MailRejected(404, "group_not_found")})
    with pytest.raises(AppError) as exc:
        await _service(client).update_mailbox(
            scope=_ADMIN, mailbox_id=7, payload=MailMailboxUpdateRequest(group_id=5)
        )

    assert exc.value.status_code == 404
    assert exc.value.code == "mail_group_not_found"


async def test_create_mailbox_409_is_not_422() -> None:
    """Регресс-гард: внешний 409 при создании ящика → 409 mail_conflict, не 422."""
    client = FakeMailClient(excs={"create_mailbox": MailRejected(409)})
    with pytest.raises(AppError) as exc:
        await _service(client).create_mailbox(scope=_ADMIN, payload=_create_payload(group_id=3))

    assert exc.value.status_code == 409


async def test_create_mailbox_missing_sync_field_maps_502() -> None:
    broken = {k: v for k, v in _MAILBOX.items() if k != "last_sync_error"}
    client = FakeMailClient(results={"create_mailbox": broken})
    with pytest.raises(AppError) as exc:
        await _service(client).create_mailbox(scope=_ADMIN, payload=_create_payload(group_id=3))

    assert exc.value.status_code == 502


async def test_update_mailbox_404_maps_mailbox_not_found() -> None:
    client = FakeMailClient(excs={"update_mailbox": MailRejected(404)})
    with pytest.raises(AppError) as exc:
        await _service(client).update_mailbox(
            scope=_ADMIN, mailbox_id=7, payload=MailMailboxUpdateRequest(is_active=False)
        )

    assert exc.value.status_code == 404
    assert exc.value.code == "mail_mailbox_not_found"


async def test_update_mailbox_409_maps_conflict() -> None:
    client = FakeMailClient(excs={"update_mailbox": MailRejected(409)})
    with pytest.raises(AppError) as exc:
        await _service(client).update_mailbox(
            scope=_ADMIN, mailbox_id=7, payload=MailMailboxUpdateRequest(email="x@y.z")
        )

    assert exc.value.code == "mail_conflict"


async def test_sync_mailbox_success_returns_queued() -> None:
    client = FakeMailClient(results={"sync_mailbox": {"queued": True}})
    result = await _service(client).sync_mailbox(scope=_ADMIN, mailbox_id=7)

    assert result.queued is True


# --------------------------------------------- MailScope: мутация ящика вне scope ----
async def test_create_mailbox_group_out_of_scope_is_403() -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _service(client).create_mailbox(scope=_SCOPE_3, payload=_create_payload(group_id=99))

    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"
    assert client.write_calls == []  # мутация наружу не ушла


async def test_create_mailbox_group_none_forbidden_for_non_admin() -> None:
    """Не-админ обязан указать group_id ∈ scope; None (без команды) недоступно → 403."""
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _service(client).create_mailbox(
            scope=_SCOPE_3, payload=_create_payload(group_id=None)
        )

    assert exc.value.status_code == 403


async def test_create_mailbox_group_in_scope_ok() -> None:
    client = FakeMailClient(results={"create_mailbox": _MAILBOX})
    result = await _service(client).create_mailbox(
        scope=_SCOPE_3, payload=_create_payload(group_id=3)
    )

    assert result.id == 7


async def test_update_mailbox_in_scope_ok() -> None:
    """Read-before-write: ящик найден среди scope-групп → мутация проходит."""
    client = FakeMailClient(results={"list_mailboxes": _MAILBOXES, "update_mailbox": _MAILBOX})
    result = await _service(client).update_mailbox(
        scope=_SCOPE_3, mailbox_id=7, payload=MailMailboxUpdateRequest(is_active=False)
    )

    assert result.id == 7
    # scope-guard дёрнул list_mailboxes по scope-группам.
    assert client.mailbox_list_calls[0]["group_ids"] == [3]


async def test_update_mailbox_out_of_scope_is_403() -> None:
    """Ящик не принадлежит scope-группам (list вернул другие id) → 403."""
    client = FakeMailClient(results={"list_mailboxes": _MAILBOXES})
    with pytest.raises(AppError) as exc:
        await _service(client).update_mailbox(
            scope=_SCOPE_3, mailbox_id=999, payload=MailMailboxUpdateRequest(is_active=False)
        )

    assert exc.value.status_code == 403
    assert not any(w[0] == "update_mailbox" for w in client.write_calls)


async def test_delete_mailbox_nonexistent_for_non_admin_is_403() -> None:
    """Несуществующий ящик для не-админа неотличим от чужого → 403 (не 404)."""
    client = FakeMailClient(results={"list_mailboxes": {"mailboxes": []}})
    with pytest.raises(AppError) as exc:
        await _service(client).delete_mailbox(scope=_SCOPE_3, mailbox_id=12345)

    assert exc.value.status_code == 403


async def test_sync_mailbox_empty_scope_is_403() -> None:
    client = FakeMailClient()
    with pytest.raises(AppError) as exc:
        await _service(client).sync_mailbox(scope=_EMPTY_SCOPE, mailbox_id=7)

    assert exc.value.status_code == 403
    assert client.mailbox_list_calls == []  # пустой scope → без внешнего вызова


async def test_scope_guard_external_down_maps_502() -> None:
    """Недоступность внешнего при read-before-write → 502 (не тихий пропуск)."""
    client = FakeMailClient(excs={"list_mailboxes": MailUnavailable("down")})
    with pytest.raises(AppError) as exc:
        await _service(client).delete_mailbox(scope=_SCOPE_3, mailbox_id=7)

    assert exc.value.status_code == 502


# ----------------------------------------------------------------- tags CRUD --------
async def test_create_tag_success() -> None:
    client = FakeMailClient(results={"create_tag": _TAG})
    result = await _service(client).create_tag(MailTagCreateRequest(name="Счета", color="#2563eb"))

    assert result.id == 7
    assert result.match_mode == "any"


async def test_create_tag_conflict_maps_409() -> None:
    client = FakeMailClient(excs={"create_tag": MailRejected(409)})
    with pytest.raises(AppError) as exc:
        await _service(client).create_tag(MailTagCreateRequest(name="Счета", color="#2563eb"))

    assert exc.value.code == "mail_conflict"


async def test_update_tag_not_found_maps_404() -> None:
    client = FakeMailClient(excs={"update_tag": MailRejected(404)})
    with pytest.raises(AppError) as exc:
        await _service(client).update_tag(1, MailTagUpdateRequest(name="Новое"))

    assert exc.value.code == "mail_tag_not_found"


async def test_delete_tag_builtin_conflict_maps_409() -> None:
    client = FakeMailClient(excs={"delete_tag": MailRejected(409)})
    with pytest.raises(AppError) as exc:
        await _service(client).delete_tag(1)

    assert exc.value.code == "mail_conflict"


async def test_create_tag_rule_success_and_not_found() -> None:
    ok = FakeMailClient(results={"create_tag_rule": _TAG_RULE})
    rule = await _service(ok).create_tag_rule(
        1, MailTagRuleCreateRequest(type="subject_contains", pattern="счёт")
    )
    assert rule.id == 12

    missing = FakeMailClient(excs={"create_tag_rule": MailRejected(404)})
    with pytest.raises(AppError) as exc:
        await _service(missing).create_tag_rule(
            1, MailTagRuleCreateRequest(type="subject_contains", pattern="счёт")
        )
    assert exc.value.code == "mail_tag_not_found"


async def test_apply_tag_to_existing_success() -> None:
    client = FakeMailClient(results={"apply_tag_to_existing": {"applied_count": 5}})
    result = await _service(client).apply_tag_to_existing(1)

    assert result.applied_count == 5


# ------------------------------------------------- test_mailbox: 422/400, никогда 502
async def test_test_mailbox_success() -> None:
    client = FakeMailClient(results={"test_mailbox": {"imap_ok": True, "smtp_ok": False}})
    result = await _service(client).test_mailbox(_test_payload())

    assert result.imap_ok is True
    assert result.smtp_ok is False


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(422, "unprocessable"), (400, "validation_error")],
)
async def test_test_mailbox_login_failure_maps_422_or_400_not_502(
    status_code: int, expected: str
) -> None:
    """Сбой IMAP/SMTP-логина на пути test → проброс внешнего 422/400, НИКОГДА не 502."""
    client = FakeMailClient(excs={"test_mailbox": MailRejected(status_code)})
    with pytest.raises(AppError) as exc:
        await _service(client).test_mailbox(_test_payload())

    assert exc.value.code == expected
    assert exc.value.status_code != 502


async def test_test_mailbox_external_down_maps_502() -> None:
    """Недоступность самого агрегатора (не логин) → 502."""
    client = FakeMailClient(excs={"test_mailbox": MailUnavailable("down")})
    with pytest.raises(AppError) as exc:
        await _service(client).test_mailbox(_test_payload())

    assert exc.value.status_code == 502


# --------------------------------- list_team_mailboxes (секция «Почты команды»)
async def test_list_team_mailboxes_null_group_empty_no_call() -> None:
    """mail_group_id=None → пустой список без вызова внешнего API (не 404/502)."""
    client = FakeMailClient(results={"list_mailboxes": _MAILBOXES})
    result = await _service(client).list_team_mailboxes(None)

    assert result.mailboxes == []
    assert client.mailbox_list_calls == []


async def test_list_team_mailboxes_disabled_empty() -> None:
    client = FakeMailClient(results={"list_mailboxes": _MAILBOXES})
    result = await _service(client, enabled=False).list_team_mailboxes(3)

    assert result.mailboxes == []
    assert client.mailbox_list_calls == []


async def test_list_team_mailboxes_success_projects_minimal() -> None:
    client = FakeMailClient(results={"list_mailboxes": _MAILBOXES})
    result = await _service(client).list_team_mailboxes(3)

    assert client.mailbox_list_calls[0]["group_ids"] == [3]
    item = result.mailboxes[0]
    assert item.id == 7
    assert item.email == "inbox@postapp.store"
    assert item.is_active is True
    # Минимальная схема TeamMailboxItem — без sync-полей/кредов.
    assert not hasattr(item, "consecutive_failures")


async def test_list_team_mailboxes_external_down_maps_502() -> None:
    client = FakeMailClient(excs={"list_mailboxes": MailUnavailable("down")})
    with pytest.raises(AppError) as exc:
        await _service(client).list_team_mailboxes(3)

    assert exc.value.status_code == 502
