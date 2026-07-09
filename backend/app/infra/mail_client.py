"""Async httpx-клиент к внешнему почтовому сервису `postapp.store`.

Модуль «Почты» — headless read+write прокси без хранения (ADR-012/ADR-038,
modules/mail). Backend подставляет секрет `MAIL_API_KEY` ТОЛЬКО в заголовок
`X-API-Key` исходящего запроса; ключ НИКОГДА не логируется и не попадает в
URL/ответы CRM (05-security.md). TLS verify включён. Тела запросов (в т.ч. транзитные
IMAP/SMTP-пароли) не логируются — в лог идут только `error_type`/`status`.

Идемпотентность ретраев (нормативно, 04-api.md#mail, ADR-038 §1):
- **GET** (`list_*`) — идемпотентны: ретрай на `ConnectError`/`ConnectTimeout`,
  read-timeout и транзиентных `{429,500,502,503,504}` (backoff `(0.2, 0.5)`).
- **POST/PATCH/DELETE** (create/update/delete/sync ящика, reply, CRUD тегов/правил,
  apply, `test`) — НЕ идемпотентны: ретрай ТОЛЬКО на ошибках установки соединения
  (запрос заведомо не ушёл). Read-timeout/`5xx` на write → сразу `MailUnavailable`
  (защита от двойной записи).

Маппинг статусов внешнего сервиса — **постатусный** (ADR-038): 2xx → JSON (или `{}` при
204); `429`/`5xx`/сеть/таймаут (исчерпаны ретраи) → `MailUnavailable`; прочий 4xx
(400/403/404/409/422) → `MailRejected(status_code)`. Различение 404 (ящик/тег/письмо),
409, 422, 400 в коды CRM выполняет сервис по контексту эндпоинта (04-api.md#mail).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, NoReturn

import httpx

from app.config import get_settings
from app.logging import get_logger

logger = get_logger(__name__)

# Задержки backoff между попытками; число попыток = len + 1 (т.е. 3).
_BACKOFF_DELAYS_SEC = (0.2, 0.5)
# Транзиентные HTTP-статусы внешнего сервиса — имеет смысл ретрай (для идемпотентных).
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_EXTERNAL_MESSAGES_PATH = "/api/external/messages"
_EXTERNAL_TEAMS_PATH = "/api/external/teams"
_EXTERNAL_MAILBOXES_PATH = "/api/external/mailboxes"
_EXTERNAL_TAGS_PATH = "/api/external/tags"
_API_KEY_HEADER = "X-API-Key"


class MailUnavailable(Exception):
    """Внешний сервис недоступен: таймаут/сеть/5xx/429/исчерпаны ретраи → 502."""


class MailRejected(Exception):
    """Внешний сервис отклонил запрос (4xx, кроме 429).

    Несёт `status_code` внешнего ответа (400/403/404/409/422) и, если внешний
    сервис прислал тело ошибки в едином формате, машиночитаемый `error_code`
    (напр. `group_not_found` при 404). Сервис маппит его в код CRM по контексту
    эндпоинта (04-api.md#mail, ADR-038): 404 `group_not_found` → команда не
    найдена, прочий 404 → ящик/тег/письмо не найдены; 409 → конфликт;
    422 → unprocessable; 400 → validation_error.
    """

    def __init__(self, status_code: int, error_code: str | None = None) -> None:
        super().__init__(str(status_code))
        self.status_code = status_code
        self.error_code = error_code


class MailClient:
    """Тонкая обёртка над external-API `postapp.store` (headless read+write прокси)."""

    def __init__(self, base_url: str, api_key: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_sec

    # --- Чтение (GET, идемпотентно) ----------------------------------------

    async def list_messages(
        self,
        *,
        order: str,
        since_id: int | None,
        before_id: int | None,
        limit: int,
        mail_account_id: int | None,
        group_ids: Sequence[int] | None,
    ) -> dict[str, Any]:
        """Лента писем: GET /api/external/messages (04-api.md#mail, ADR-013/017/038).

        `order` (`asc`/`desc`) передаётся всегда явно. `since_id` — только при `asc`;
        `before_id` — только при `desc`. Фильтры `mail_account_id` (single) и `group_ids`
        (повторяемый) **AND-комбинируемы** (external ADR-0039 §3 — взаимоисключение
        ADR-0037 снято): передаются вместе, если заданы. Идемпотентен.
        """
        params: dict[str, Any] = {"order": order, "limit": limit}
        if order == "asc" and since_id is not None:
            params["since_id"] = since_id
        elif order == "desc" and before_id is not None:
            params["before_id"] = before_id
        if mail_account_id is not None:
            params["mail_account_id"] = mail_account_id
        if group_ids:
            params["group_id"] = list(group_ids)
        return await self._request("GET", _EXTERNAL_MESSAGES_PATH, params=params, idempotent=True)

    async def list_teams(self) -> dict[str, Any]:
        """Список команд: GET /api/external/teams (04-api.md#mail). Идемпотентен."""
        return await self._request("GET", _EXTERNAL_TEAMS_PATH, idempotent=True)

    async def list_mailboxes(
        self,
        *,
        is_active: bool | None = None,
        group_ids: Sequence[int] | None = None,
    ) -> dict[str, Any]:
        """Список ящиков: GET /api/external/mailboxes (04-api.md#mail, ADR-0039 §4).

        `is_active` (опц.: `True`/`False`/None=все) и повторяемый `group_id`
        пробрасываются во внешний API. Идемпотентен.
        """
        params: dict[str, Any] = {}
        if is_active is not None:
            params["is_active"] = is_active
        if group_ids:
            params["group_id"] = list(group_ids)
        return await self._request(
            "GET", _EXTERNAL_MAILBOXES_PATH, params=params or None, idempotent=True
        )

    async def list_tags(self) -> dict[str, Any]:
        """Список глобальных тегов: GET /api/external/tags (04-api.md#mail). Идемпотентен."""
        return await self._request("GET", _EXTERNAL_TAGS_PATH, idempotent=True)

    # --- Запись (POST/PATCH/DELETE, НЕ идемпотентно) ------------------------

    async def reply(self, message_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Ответ на письмо: POST /api/external/messages/{id}/reply (не идемпотентно)."""
        path = f"{_EXTERNAL_MESSAGES_PATH}/{message_id}/reply"
        return await self._request("POST", path, json_body=payload, idempotent=False)

    async def test_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Проверка IMAP/SMTP-соединения без сохранения: POST /mailboxes/test.

        Мутирующая семантика по ретраям (открывает IMAP/SMTP-сессию) — ретрай только
        на ошибках соединения (ADR-038 §1). Путь `test` внешнего сервиса отдаёт 422/400
        и НИКОГДА не 502 (502 — только фактическая отправка).
        """
        path = f"{_EXTERNAL_MAILBOXES_PATH}/test"
        return await self._request("POST", path, json_body=payload, idempotent=False)

    async def create_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Создание ящика: POST /api/external/mailboxes (не идемпотентно)."""
        return await self._request(
            "POST", _EXTERNAL_MAILBOXES_PATH, json_body=payload, idempotent=False
        )

    async def update_mailbox(self, mailbox_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Правка ящика: PATCH /api/external/mailboxes/{id} (не идемпотентно)."""
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}"
        return await self._request("PATCH", path, json_body=payload, idempotent=False)

    async def delete_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        """Удаление ящика: DELETE /api/external/mailboxes/{id} (204, не идемпотентно)."""
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}"
        return await self._request("DELETE", path, idempotent=False)

    async def sync_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        """Форс-синк ящика: POST /api/external/mailboxes/{id}/sync (202, не идемпотентно)."""
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}/sync"
        return await self._request("POST", path, idempotent=False)

    async def create_tag(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Создание тега: POST /api/external/tags (не идемпотентно)."""
        return await self._request("POST", _EXTERNAL_TAGS_PATH, json_body=payload, idempotent=False)

    async def update_tag(self, tag_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Правка тега: PATCH /api/external/tags/{id} (не идемпотентно)."""
        path = f"{_EXTERNAL_TAGS_PATH}/{tag_id}"
        return await self._request("PATCH", path, json_body=payload, idempotent=False)

    async def delete_tag(self, tag_id: int) -> dict[str, Any]:
        """Удаление тега: DELETE /api/external/tags/{id} (204, не идемпотентно)."""
        path = f"{_EXTERNAL_TAGS_PATH}/{tag_id}"
        return await self._request("DELETE", path, idempotent=False)

    async def create_tag_rule(self, tag_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Добавление правила: POST /api/external/tags/{id}/rules (не идемпотентно)."""
        path = f"{_EXTERNAL_TAGS_PATH}/{tag_id}/rules"
        return await self._request("POST", path, json_body=payload, idempotent=False)

    async def delete_tag_rule(self, tag_id: int, rule_id: int) -> dict[str, Any]:
        """Удаление правила: DELETE /api/external/tags/{id}/rules/{rule_id} (204)."""
        path = f"{_EXTERNAL_TAGS_PATH}/{tag_id}/rules/{rule_id}"
        return await self._request("DELETE", path, idempotent=False)

    async def apply_tag_to_existing(self, tag_id: int) -> dict[str, Any]:
        """Применить тег к существующим: POST /tags/{id}/apply-to-existing (не идемпотентно).

        Идемпотентен на стороне агрегатора (`ON CONFLICT DO NOTHING`), но семантически
        дорог → политика write (ретрай только connect, ADR-038 §1).
        """
        path = f"{_EXTERNAL_TAGS_PATH}/{tag_id}/apply-to-existing"
        return await self._request("POST", path, idempotent=False)

    # --- Транспорт ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotent: bool,
    ) -> dict[str, Any]:
        """Выполняет запрос с ограниченными ретраями; ключ — только в заголовке.

        Секрет `MAIL_API_KEY` и тело запроса не логируются. Возвращает распарсенный
        JSON-объект (dict) при 2xx с телом, `{}` при 204/пустом теле; иначе бросает
        `MailUnavailable`/`MailRejected` (постатусный маппинг, ADR-038).
        """
        url = f"{self._base_url}{path}"
        headers = {_API_KEY_HEADER: self._api_key}
        max_attempts = len(_BACKOFF_DELAYS_SEC) + 1

        async with httpx.AsyncClient(timeout=self._timeout, verify=True) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.request(
                        method, url, params=params, json=json_body, headers=headers
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    # Соединение не установлено → запрос не отправлен: повтор безопасен
                    # даже для неидемпотентных write-методов.
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "mail_request_failed", error_type=type(exc).__name__, attempt=attempt + 1
                    )
                    raise MailUnavailable(str(exc)) from exc
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    # Запрос мог быть отправлен (read-timeout/сетевой сбой):
                    # ретраим только идемпотентные (GET), write → сразу 502.
                    if idempotent and attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "mail_request_failed", error_type=type(exc).__name__, attempt=attempt + 1
                    )
                    raise MailUnavailable(str(exc)) from exc
                except httpx.HTTPError as exc:
                    # Прочая ошибка httpx — неретраябельна.
                    logger.warning("mail_request_failed", error_type=type(exc).__name__)
                    raise MailUnavailable(str(exc)) from exc

                status_code = response.status_code
                if 200 <= status_code < 300:
                    return self._parse_body(response)
                if status_code in _RETRYABLE_STATUS and idempotent and attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                self._raise_for_status(response)

        # Недостижимо: цикл либо возвращает результат, либо бросает исключение.
        raise MailUnavailable("Mail request failed after retries")

    @staticmethod
    def _parse_body(response: httpx.Response) -> dict[str, Any]:
        """Парсит JSON-объект ответа; 204/пустое тело → `{}`; иначе dict.

        Нераспознаваемое/не-объектное тело → MailUnavailable.
        """
        if response.status_code == httpx.codes.NO_CONTENT or not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("mail_response_parse_failed")
            raise MailUnavailable("Некорректный ответ почтового сервиса") from exc
        if not isinstance(payload, dict):
            logger.warning("mail_response_unexpected_shape")
            raise MailUnavailable("Некорректный ответ почтового сервиса")
        return payload

    @staticmethod
    def _extract_error_code(response: httpx.Response) -> str | None:
        """Достаёт `error.code` из тела ошибки внешнего сервиса (единый формат).

        Внешний контракт (ADR-0039) отдаёт `{"error": {"code": "...", ...}}`. Код
        нужен сервису, чтобы различать 404 по семантике (напр. `group_not_found`
        vs неизвестный `id` ящика). Тело ошибки в ответ CRM не пробрасывается —
        только машиночитаемый `code`. Отсутствие/битое тело → None (best-effort).
        """
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        code = error.get("code")
        return code if isinstance(code, str) else None

    @classmethod
    def _raise_for_status(cls, response: httpx.Response) -> NoReturn:
        """Постатусный маппинг не-2xx внешнего статуса (ADR-038).

        `429`/`5xx` → недоступность (`MailUnavailable`); прочий 4xx
        (400/403/404/409/422) → `MailRejected(status_code, error_code)` для
        контекстного маппинга сервисом. Тело ошибки внешнего сервиса в CRM не
        пробрасывается — только машиночитаемый `code` (04-api.md#mail).
        """
        status_code = response.status_code
        if status_code == httpx.codes.TOO_MANY_REQUESTS or status_code >= 500:
            logger.warning("mail_request_failed", status=status_code)
            raise MailUnavailable(f"Внешний сервис вернул {status_code}")
        raise MailRejected(status_code, cls._extract_error_code(response))


def get_mail_client() -> MailClient:
    """Фабрика клиента почты из настроек (base/ключ/таймаут — 07-deployment.md)."""
    settings = get_settings()
    return MailClient(
        base_url=settings.mail_api_base,
        api_key=settings.mail_api_key,
        timeout_sec=settings.mail_api_timeout_sec,
    )


__all__ = [
    "MailClient",
    "MailRejected",
    "MailUnavailable",
    "get_mail_client",
]
