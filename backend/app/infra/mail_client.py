"""Async httpx-клиент к внешнему почтовому сервису `postapp.store`.

Модуль «Почты» — read-through-прокси без хранения (ADR-012, modules/mail). Backend
подставляет секрет `MAIL_API_KEY` ТОЛЬКО в заголовок `X-API-Key` исходящего запроса;
ключ НИКОГДА не логируется и не попадает в URL/ответы CRM (05-security.md). TLS verify
включён. Транзиентные ошибки (сеть/таймаут/5xx) → `MailUnavailable` (модель ретраев —
как у `app/infra/prometheus.py` / `app/infra/ai_provider.py`).

Идемпотентность: `list_messages` (GET) — идемпотентен, ретраится на любой транзиентной
ошибке. `reply` (POST, отправка письма) — НЕ идемпотентен: ретраится только на ошибках
установки соединения (запрос заведомо не отправлен), но НЕ на read-timeout/5xx (запрос
мог быть доставлен) — чтобы не отправить письмо повторно.
"""

from __future__ import annotations

import asyncio
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
_API_KEY_HEADER = "X-API-Key"


class MailUnavailable(Exception):
    """Внешний сервис недоступен: таймаут/сеть/5xx/429/исчерпаны ретраи → 502."""


class MailMessageNotFound(Exception):
    """Внешний сервис вернул 404 (письмо не найдено при reply) → 404."""


class MailRejected(Exception):
    """Внешний сервис отклонил запрос как невалидный (4xx, кроме 404/429).

    Несёт `status_code` внешнего ответа: reply → `422 unprocessable`; на идемпотентном
    list внешний `400` (взаимоисключение режимов пагинации) маппится в
    `400 validation_error` (04-api.md#mail).
    """

    def __init__(self, status_code: int) -> None:
        super().__init__(str(status_code))
        self.status_code = status_code


class MailClient:
    """Тонкая обёртка над external-API `postapp.store` (read-through-прокси)."""

    def __init__(self, base_url: str, api_key: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_sec

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
        """Лента писем: GET /api/external/messages (04-api.md#mail, ADR-013/ADR-017).

        `order` (`asc`/`desc`) передаётся во внешний API **всегда явно** (не полагаемся
        на внешний default). `since_id` — только при `order=asc` (keyset вперёд по
        `id ASC`); `before_id` — только при `order=desc` (backward по `id DESC`).
        Серверные фильтры `mail_account_id`/`group_id` (external ADR-0037) —
        взаимоисключающи (проверено в сервисе до вызова): пробрасывается лишь заданный.
        Идемпотентен — ретраится на транзиентных ошибках.
        """
        params: dict[str, Any] = {"order": order, "limit": limit}
        if order == "asc" and since_id is not None:
            params["since_id"] = since_id
        elif order == "desc" and before_id is not None:
            params["before_id"] = before_id
        if mail_account_id is not None:
            params["mail_account_id"] = mail_account_id
        elif group_id is not None:
            params["group_id"] = group_id
        return await self._request("GET", _EXTERNAL_MESSAGES_PATH, params=params, idempotent=True)

    async def list_teams(self) -> dict[str, Any]:
        """Список команд: GET /api/external/teams (04-api.md#mail, ADR-017).

        GET, идемпотентен — ретраится на транзиентных ошибках, как `list_messages`.
        """
        return await self._request("GET", _EXTERNAL_TEAMS_PATH, idempotent=True)

    async def list_mailboxes(self) -> dict[str, Any]:
        """Список почтовых ящиков: GET /api/external/mailboxes (04-api.md#mail, ADR-017).

        GET, идемпотентен — ретраится на транзиентных ошибках, как `list_messages`.
        """
        return await self._request("GET", _EXTERNAL_MAILBOXES_PATH, idempotent=True)

    async def reply(self, message_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Ответ на письмо: POST /api/external/messages/{id}/reply.

        НЕ идемпотентен (отправка письма) — ретраится только на ошибках установки
        соединения, но не на read-timeout/5xx (см. модульный docstring).
        """
        path = f"{_EXTERNAL_MESSAGES_PATH}/{message_id}/reply"
        return await self._request("POST", path, json_body=payload, idempotent=False)

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

        Секрет `MAIL_API_KEY` не логируется и не попадает в URL. Возвращает
        распарсенный JSON-объект (dict) при 2xx; иначе бросает типизированное
        исключение модуля (`MailUnavailable`/`MailMessageNotFound`/`MailRejected`).
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
                    # даже для неидемпотентного reply.
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "mail_request_failed", error_type=type(exc).__name__, attempt=attempt + 1
                    )
                    raise MailUnavailable(str(exc)) from exc
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    # Запрос мог быть отправлен (read-timeout/сетевой сбой):
                    # ретраим только идемпотентные (list), для reply — сразу отдаём 502.
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
                    return self._parse_json(response)
                if status_code in _RETRYABLE_STATUS and idempotent and attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                self._raise_for_status(status_code)

        # Недостижимо: цикл либо возвращает результат, либо бросает исключение.
        raise MailUnavailable("Mail request failed after retries")

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        """Парсит JSON-объект ответа; нераспознаваемое тело → MailUnavailable."""
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
    def _raise_for_status(status_code: int) -> NoReturn:
        """Маппит не-2xx статус внешнего сервиса в типизированное исключение модуля.

        404 → not found; 429/5xx → недоступность; прочий 4xx → отклонение (валидация).
        Тело ошибки внешнего сервиса в CRM не пробрасывается (04-api.md#mail).
        """
        if status_code == httpx.codes.NOT_FOUND:  # 404
            raise MailMessageNotFound(str(status_code))
        if status_code == httpx.codes.TOO_MANY_REQUESTS or status_code >= 500:
            logger.warning("mail_request_failed", status=status_code)
            raise MailUnavailable(f"Внешний сервис вернул {status_code}")
        # Прочий 4xx — внешний сервис отклонил запрос (невалидное тело reply /
        # взаимоисключение режимов пагинации при list); статус несём для маппинга.
        raise MailRejected(status_code)


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
    "MailMessageNotFound",
    "MailRejected",
    "MailUnavailable",
    "get_mail_client",
]
