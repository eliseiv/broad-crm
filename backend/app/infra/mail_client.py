"""Async httpx-клиент к агрегатору-connector'у `postapp.store` (ADR-044 §1/§4/§8).

В модели ADR-044 CRM — система-запись: лента/теги/ящики читаются из БД CRM, а агрегатор
остаётся тонким mail-connector'ом. Через этот клиент CRM делает ТОЛЬКО управляющие
вызовы жизненного цикла ящика (create/update/delete/sync/test — креды транзитом,
шифрование в агрегаторе) и делегирование SMTP-отправки reply (`send`). GET-чтения,
message-scoped reply и CRUD тегов агрегатору больше не проксируются (эти эндпоинты в
агрегаторе сняты, `ADR-0043`).

Backend подставляет секрет `MAIL_API_KEY` ТОЛЬКО в заголовок `X-API-Key`; ключ никогда
не логируется и не попадает в URL/ответы CRM (05-security.md). TLS verify включён. Тела
запросов (транзитные IMAP/SMTP-пароли) не логируются — в лог идут только
`error_type`/`status`.

Идемпотентность ретраев (ADR-044 §4, инвариант ADR-038 §1):
- Все вызовы здесь — **write** (create/update/delete/sync/test/send): НЕ идемпотентны.
  Ретрай ТОЛЬКО на ошибках установки соединения (запрос заведомо не ушёл). Read-timeout/
  `5xx` на write → сразу `MailUnavailable` (защита от двойной записи/отправки).

Маппинг статусов: 2xx → JSON (или `{}` при 204); `429`/`5xx`/сеть/таймаут → `MailUnavailable`;
прочий 4xx (400/403/404/409/422) → `MailRejected(status_code)`. Различение в коды CRM
выполняет сервис по контексту эндпоинта (ADR-044 §4/§8).
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

_EXTERNAL_MAILBOXES_PATH = "/api/external/mailboxes"
_EXTERNAL_OAUTH_AUTHORIZE_PATH = f"{_EXTERNAL_MAILBOXES_PATH}/oauth/authorize"
_API_KEY_HEADER = "X-API-Key"


class MailUnavailable(Exception):
    """Агрегатор недоступен: таймаут/сеть/5xx/429/исчерпаны ретраи → 502."""


class MailRejected(Exception):
    """Агрегатор отклонил запрос (4xx, кроме 429).

    Несёт `status_code` внешнего ответа (400/403/404/409/422) и, если агрегатор прислал
    тело ошибки в едином формате, машиночитаемый `error_code`. Сервис маппит его в код
    CRM по контексту эндпоинта (ADR-044 §4/§8): 404 → ящик не найден; 409 → конфликт;
    422 → unprocessable; 400 → validation_error.
    """

    def __init__(self, status_code: int, error_code: str | None = None) -> None:
        super().__init__(str(status_code))
        self.status_code = status_code
        self.error_code = error_code


class MailClient:
    """Тонкая обёртка над управляющим external-API агрегатора (ADR-044 §4/§8)."""

    def __init__(self, base_url: str, api_key: str, timeout_sec: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_sec

    # --- Жизненный цикл ящика (write, НЕ идемпотентно) ---------------------

    async def test_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Проверка IMAP/SMTP-соединения без сохранения: POST /mailboxes/test.

        Мутирующая семантика по ретраям (открывает IMAP/SMTP-сессию) — ретрай только
        на ошибках соединения. Путь `test` агрегатора отдаёт 422/400 и НИКОГДА не 502.
        """
        path = f"{_EXTERNAL_MAILBOXES_PATH}/test"
        return await self._request("POST", path, json_body=payload)

    async def create_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Создание ящика: POST /api/external/mailboxes → `{id, ...}` (не идемпотентно).

        Владелец в агрегаторе = служебный `crm-service` (без `group_id`); привязка к
        команде живёт только в CRM-каталоге (ADR-044 §4). Возвращает присвоенный `id`.
        """
        return await self._request("POST", _EXTERNAL_MAILBOXES_PATH, json_body=payload)

    async def update_mailbox(self, mailbox_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Правка ящика: PATCH /api/external/mailboxes/{id} (не идемпотентно)."""
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}"
        return await self._request("PATCH", path, json_body=payload)

    async def delete_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        """Удаление ящика: DELETE /api/external/mailboxes/{id} (204, не идемпотентно)."""
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}"
        return await self._request("DELETE", path)

    async def sync_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        """Форс-синк ящика: POST /api/external/mailboxes/{id}/sync (202, не идемпотентно)."""
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}/sync"
        return await self._request("POST", path)

    async def authorize_oauth(self, crm_state: str) -> dict[str, Any]:
        """Запросить у агрегатора Microsoft authorize URL (ADR-045 §2).

        POST /api/external/mailboxes/oauth/authorize `{crm_state}` → `{authorize_url,
        state}`. Outlook-OAuth выключен на агрегаторе (нет `OUTLOOK_CLIENT_ID`/`_SECRET`)
        → внешний `404` → `MailRejected(404)` (сервис маппит в 503 mail_not_configured,
        ADR-045 §3). Мутирующая семантика ретраев (минт `state` в Redis) — ретрай только
        на ошибках соединения. `crm_state` — непрозрачный подписанный CRM-токен.
        """
        return await self._request(
            "POST", _EXTERNAL_OAUTH_AUTHORIZE_PATH, json_body={"crm_state": crm_state}
        )

    async def send_message(self, mailbox_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Отправка reply: POST /api/external/mailboxes/{id}/send (ADR-044 §8).

        Тело `{to, cc, subject, body_text, in_reply_to?, refs?}` → `{sent_id,
        smtp_message_id}`. Обобщённый send-эндпоинт заменяет message-scoped reply
        (письма живут в CRM, threading формирует CRM). Не идемпотентно (SMTP-отправка).
        """
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}/send"
        return await self._request("POST", path, json_body=payload)

    # --- Транспорт ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Выполняет write-запрос с ретраем только на ошибках соединения; ключ — в заголовке.

        Секрет `MAIL_API_KEY` и тело запроса не логируются. Возвращает распарсенный
        JSON-объект (dict) при 2xx с телом, `{}` при 204/пустом теле; иначе бросает
        `MailUnavailable`/`MailRejected` (постатусный маппинг, ADR-044 §4).
        """
        url = f"{self._base_url}{path}"
        headers = {_API_KEY_HEADER: self._api_key}
        max_attempts = len(_BACKOFF_DELAYS_SEC) + 1

        async with httpx.AsyncClient(timeout=self._timeout, verify=True) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.request(method, url, json=json_body, headers=headers)
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
                    # Запрос мог быть отправлен (read-timeout/сетевой сбой): write →
                    # сразу 502 (защита от двойной записи/отправки).
                    logger.warning(
                        "mail_request_failed", error_type=type(exc).__name__, attempt=attempt + 1
                    )
                    raise MailUnavailable(str(exc)) from exc
                except httpx.HTTPError as exc:
                    logger.warning("mail_request_failed", error_type=type(exc).__name__)
                    raise MailUnavailable(str(exc)) from exc

                status_code = response.status_code
                if 200 <= status_code < 300:
                    return self._parse_body(response)
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
        """Достаёт `error.code` из тела ошибки агрегатора (единый формат).

        Тело ошибки в ответ CRM не пробрасывается — только машиночитаемый `code`.
        Отсутствие/битое тело → None (best-effort).
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
        """Постатусный маппинг не-2xx статуса (ADR-044 §4).

        `429`/`5xx` → недоступность (`MailUnavailable`); прочий 4xx
        (400/403/404/409/422) → `MailRejected(status_code, error_code)`.
        """
        status_code = response.status_code
        if status_code == httpx.codes.TOO_MANY_REQUESTS or status_code >= 500:
            logger.warning("mail_request_failed", status=status_code)
            raise MailUnavailable(f"Агрегатор вернул {status_code}")
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
