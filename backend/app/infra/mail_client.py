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

Бюджеты вызова (ADR-053 §1.2, паттерн ADR-024 — ОБЕ половины):
- `httpx.Timeout` ПО ФАЗАМ: `connect`/`write`/`pool` — фиксированные константы кода,
  `read` — бюджет КАТЕГОРИИ клиента (быстрый / mail-server). Одиночный float отменён:
  он не отличает установку соединения от ожидания ответа.
- `asyncio.wait_for` — overall-deadline вокруг ВСЕГО вызова (все попытки ретрая +
  backoff-паузы + все фазы каждой попытки). Без него per-phase лимиты суммарной границы
  не дают (worst-case `connect+write+read` × 3 попытки уходит за `proxy_read_timeout`
  nginx → пользователь получает HTML-`504` прокси вместо JSON CRM).
- Пер-вызовный override overall-deadline (`deadline_sec`) — им пользуется компенсирующая
  уборка сироты (короткий бюджет, ADR-053 §1.2.2).

Идемпотентность ретраев (ADR-044 §4, инвариант ADR-038 §1) — БЕЗ ИЗМЕНЕНИЙ:
- Все вызовы здесь — **write** (create/update/delete/sync/test/send): НЕ идемпотентны.
  Ретрай ТОЛЬКО на ошибках установки соединения (`ConnectError`/`ConnectTimeout` —
  запрос заведомо не ушёл). Read-timeout / исчерпание deadline / `504` / `5xx` на write →
  сразу ошибка, БЕЗ повтора (защита от двойной записи/отправки). Долгий бюджет НЕ делает
  write идемпотентным.

Маппинг статусов (ADR-053 §1.3 п.4): 2xx → JSON (или `{}` при 204); `504` (прокси
агрегатора не дождался) → `MailTimeout(status_code=504)`; собственный таймаут CRM
(read-фаза httpx / исчерпание overall-deadline) → `MailTimeout(status_code=None)`;
`429`/прочие `5xx` → `MailUnavailable(status_code, error_code)`; прочий 4xx
(400/403/404/409/422) → `MailRejected(status_code, error_code)`. `status_code`/`error_code`
переносятся во ВСЕ не-2xx ветки — иначе `502 smtp_failed` неотличим от «агрегатор упал»,
а `504` его прокси — от собственного таймаута CRM. Различение в коды CRM выполняет
сервис по контексту эндпоинта (ADR-053 §2/§2.1).
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

# Фазовые константы `httpx.Timeout` (ADR-053 §1.2, НЕ env).
# `connect` = 5: агрегатор — известный хост с постоянным адресом; долгий connect
# умножается на 3 попытки ретрая и съедает весь deadline, не оставив времени на ЧТЕНИЕ.
_CONNECT_TIMEOUT_SEC = 5.0
# `write` = 10: исходящее тело мало (креды; тело reply ≤ 1 MiB).
_WRITE_TIMEOUT_SEC = 10.0
_POOL_TIMEOUT_SEC = 5.0

_EXTERNAL_MAILBOXES_PATH = "/api/external/mailboxes"
_EXTERNAL_OAUTH_AUTHORIZE_PATH = f"{_EXTERNAL_MAILBOXES_PATH}/oauth/authorize"
_API_KEY_HEADER = "X-API-Key"


class MailUnavailable(Exception):
    """Агрегатор недоступен: сеть/`5xx`/`429`/исчерпаны ретраи/битое тело → 502.

    Несёт `status_code` внешнего ответа и машиночитаемый `error_code` (если агрегатор
    прислал тело ошибки в едином формате) — ADR-053 §1.3 п.3. Без них `502 smtp_failed`
    (удалённый SMTP отклонил письмо, агрегатор РАБОТАЛ) неотличим от падения агрегатора.
    Транспортные ветки (сеть/битое тело) оставляют оба поля `None`.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class MailTimeout(Exception):
    """Операция не завершилась вовремя — это НЕ «сервис недоступен» (ADR-053 §1.3 п.1/§3).

    `status_code` различает ИСТОЧНИК таймаута (ADR-053 §1.3 п.2 — без этого поля §2.1
    нереализуема):
    - `504` — таймаут пришёл ОТ агрегатора (его прокси не дождался): агрегатор доступен
      и сам сообщает «не успел»;
    - `None` — СОБСТВЕННЫЙ таймаут CRM: read-фаза `httpx` или исчерпание overall-deadline
      `asyncio.wait_for`; до CRM ничего не дошло.

    Не ретраится (анти-двойная-запись/отправка, ADR-038 §1).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class MailRejected(Exception):
    """Агрегатор отклонил запрос (4xx, кроме 429).

    Несёт `status_code` внешнего ответа (400/403/404/409/422) и, если агрегатор прислал
    тело ошибки в едином формате, машиночитаемый `error_code`. Сервис маппит его в код
    CRM по контексту эндпоинта (ADR-044 §4/§8, ADR-053 §2): 404 → ящик не найден; 409 →
    конфликт; 422 + `imap_login_failed`/`smtp_login_failed`/`invalid_host` → конкретная
    причина отказа проверки; прочие 422 → unprocessable; 400 → validation_error.
    """

    def __init__(self, status_code: int, error_code: str | None = None) -> None:
        super().__init__(str(status_code))
        self.status_code = status_code
        self.error_code = error_code


class MailClient:
    """Тонкая обёртка над управляющим external-API агрегатора (ADR-044 §4/§8).

    Экземпляр несёт бюджеты ОДНОЙ категории путей (ADR-053 §1.3 п.6): `read_timeout_sec`
    (read-фаза одной попытки) и `deadline_sec` (overall-деадлайн всего вызова). Категорию
    выбирает СЕРВИС (он знает эндпоинт) — фабриками `get_mail_client` (быстрые пути) и
    `get_mail_server_client` (mail-server-пути); транспорт остаётся «тупым».
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        read_timeout_sec: float,
        deadline_sec: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._read_timeout_sec = read_timeout_sec
        self._deadline_sec = deadline_sec

    # --- Жизненный цикл ящика (write, НЕ идемпотентно) ---------------------

    async def test_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Проверка IMAP/SMTP-соединения без сохранения: POST /mailboxes/test.

        Mail-server-путь (ADR-053 §1.1): агрегатор идёт на УДАЛЁННЫЙ IMAP/SMTP и законно
        тратит десятки секунд. Мутирующая семантика по ретраям (открывает IMAP/SMTP-сессию)
        — ретрай только на ошибках соединения.
        """
        path = f"{_EXTERNAL_MAILBOXES_PATH}/test"
        return await self._request("POST", path, json_body=payload)

    async def create_mailbox(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Создание ящика: POST /api/external/mailboxes → `{id, ...}` (не идемпотентно).

        Mail-server-путь: агрегатор прогоняет connection-test до вставки (ADR-053 §1.1).
        Владелец в агрегаторе = служебный `crm-service` (без `group_id`); привязка к
        команде живёт только в CRM-каталоге (ADR-044 §4). Возвращает присвоенный `id`.
        """
        return await self._request("POST", _EXTERNAL_MAILBOXES_PATH, json_body=payload)

    async def update_mailbox(self, mailbox_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """Правка ящика: PATCH /api/external/mailboxes/{id} (не идемпотентно).

        Mail-server-путь: агрегатор ре-тестит креды при их правке; CRM не знает, ре-тестит
        ли он на конкретном теле, поэтому ЛЮБОЙ вызов эндпоинта идёт по долгому бюджету
        (ADR-053 §1.1).
        """
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}"
        return await self._request("PATCH", path, json_body=payload)

    async def delete_mailbox(
        self, mailbox_id: int, *, deadline_sec: float | None = None
    ) -> dict[str, Any]:
        """Удаление ящика: DELETE /api/external/mailboxes/{id} (204, не идемпотентно).

        Быстрый путь. `deadline_sec` — пер-вызовный override overall-deadline (ADR-053
        §1.2.2): компенсирующая уборка сироты обязана уложиться в короткий бюджет, иначе
        сумма вызовов запроса `create` выйдет за `proxy_read_timeout` nginx.
        """
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}"
        return await self._request("DELETE", path, deadline_sec=deadline_sec)

    async def sync_mailbox(self, mailbox_id: int) -> dict[str, Any]:
        """Форс-синк ящика: POST /api/external/mailboxes/{id}/sync (202, не идемпотентно).

        Быстрый путь: агрегатор лишь ставит задачу в очередь (ADR-053 §1.1).
        """
        path = f"{_EXTERNAL_MAILBOXES_PATH}/{mailbox_id}/sync"
        return await self._request("POST", path)

    async def authorize_oauth(self, crm_state: str) -> dict[str, Any]:
        """Запросить у агрегатора Microsoft authorize URL (ADR-045 §2). Быстрый путь.

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
        """Отправка reply: POST /api/external/mailboxes/{id}/send (ADR-044 §8, ADR-057 §2).

        Mail-server-путь: агрегатор идёт на удалённый SMTP (+ IMAP APPEND) и законно
        тратит до ~55 с (ADR-053 §1.1). Тело `{to, cc, subject, body_text, in_reply_to?,
        refs?}` → **`{smtp_message_id}`** (`sent_id` агрегатор НЕ возвращает — ADR-057 §2;
        публичный `sent_id` CRM берёт из своей `mail_sent_messages`). Не идемпотентно
        (SMTP-отправка). Внешний `404` = ящика `{id}` нет в агрегаторе (не «письма нет»).
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
        deadline_sec: float | None = None,
    ) -> dict[str, Any]:
        """Вызов к агрегатору под overall-deadline (ADR-053 §1.2, паттерн ADR-024).

        `asyncio.wait_for` ограничивает ВЕСЬ вызов — все попытки ретрая, backoff-паузы и
        все фазы каждой попытки. Исчерпание = собственный таймаут CRM →
        `MailTimeout(status_code=None)`, не «сервис недоступен» и не повод для ретрая.
        """
        deadline = self._deadline_sec if deadline_sec is None else deadline_sec
        try:
            return await asyncio.wait_for(
                self._request_with_retries(method, path, json_body=json_body),
                timeout=deadline,
            )
        except TimeoutError as exc:
            # asyncio.TimeoutError (== builtin TimeoutError с 3.11): исчерпан
            # overall-deadline. Секрет/тело не логируются.
            logger.warning("mail_request_deadline_exceeded", deadline_sec=deadline)
            raise MailTimeout(
                "Почтовый сервис не ответил за отведённое время", status_code=None
            ) from exc

    async def _request_with_retries(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Выполняет write-запрос с ретраем только на ошибках соединения; ключ — в заголовке.

        Секрет `MAIL_API_KEY` и тело запроса не логируются. Возвращает распарсенный
        JSON-объект (dict) при 2xx с телом, `{}` при 204/пустом теле; иначе бросает
        `MailTimeout`/`MailUnavailable`/`MailRejected` (постатусный маппинг, ADR-053 §1.3).
        """
        url = f"{self._base_url}{path}"
        headers = {_API_KEY_HEADER: self._api_key}
        max_attempts = len(_BACKOFF_DELAYS_SEC) + 1
        timeout = httpx.Timeout(
            connect=_CONNECT_TIMEOUT_SEC,
            read=self._read_timeout_sec,
            write=_WRITE_TIMEOUT_SEC,
            pool=_POOL_TIMEOUT_SEC,
        )

        async with httpx.AsyncClient(timeout=timeout, verify=True) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.request(method, url, json=json_body, headers=headers)
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    # Соединение не установлено → запрос не отправлен: повтор безопасен
                    # даже для неидемпотентных write-методов (ADR-038 §1).
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "mail_request_failed", error_type=type(exc).__name__, attempt=attempt + 1
                    )
                    raise MailUnavailable(str(exc)) from exc
                except httpx.TimeoutException as exc:
                    # Собственный таймаут CRM (read/write/pool-фаза): запрос МОГ уйти →
                    # НЕ ретраим (защита от двойной записи/отправки), но и не выдаём
                    # доступный агрегатор за упавший — это MailTimeout, не MailUnavailable
                    # (ADR-053 §3). `ConnectTimeout` сюда не попадает — он выше.
                    logger.warning(
                        "mail_request_timeout", error_type=type(exc).__name__, attempt=attempt + 1
                    )
                    raise MailTimeout(str(exc), status_code=None) from exc
                except httpx.TransportError as exc:
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
        """Постатусный маппинг не-2xx статуса (ADR-053 §1.3 п.4).

        `504` → `MailTimeout(status_code=504)` (таймаут ОТ агрегатора: его прокси не
        дождался, сам агрегатор доступен); `429`/прочие `5xx` → `MailUnavailable(
        status_code, error_code)`; прочий 4xx (400/403/404/409/422) → `MailRejected(
        status_code, error_code)`. `error_code` извлекается во ВСЕХ ветках, а не только
        в `MailRejected` — иначе `502 smtp_failed` неотличим от падения агрегатора.
        """
        status_code = response.status_code
        error_code = cls._extract_error_code(response)
        if status_code == httpx.codes.GATEWAY_TIMEOUT:
            logger.warning("mail_request_timeout", status=status_code)
            raise MailTimeout(
                f"Агрегатор вернул {status_code}",
                status_code=status_code,
                error_code=error_code,
            )
        if status_code == httpx.codes.TOO_MANY_REQUESTS or status_code >= 500:
            logger.warning("mail_request_failed", status=status_code)
            raise MailUnavailable(
                f"Агрегатор вернул {status_code}",
                status_code=status_code,
                error_code=error_code,
            )
        raise MailRejected(status_code, error_code)


def get_mail_client() -> MailClient:
    """Клиент БЫСТРОЙ категории путей (ADR-053 §1.1/§1.3 п.6).

    `delete` / `sync` / `oauth-authorize` — агрегатор отвечает из своей БД/Redis:
    read-бюджет `MAIL_API_TIMEOUT_SEC`, overall-deadline `MAIL_API_DEADLINE_SEC`.
    """
    settings = get_settings()
    return MailClient(
        base_url=settings.mail_api_base,
        api_key=settings.mail_api_key,
        read_timeout_sec=settings.mail_api_timeout_sec,
        deadline_sec=settings.mail_api_deadline_sec,
    )


def get_mail_server_client() -> MailClient:
    """Клиент MAIL-SERVER категории путей (ADR-053 §1.1/§1.3 п.6).

    `test` / `create` / `patch` / `send` — агрегатор идёт на УДАЛЁННЫЙ IMAP/SMTP:
    read-бюджет `MAIL_API_MAILSERVER_TIMEOUT_SEC` (обязан превышать потолок ответа
    агрегатора — 60 с его nginx), overall-deadline `MAIL_API_MAILSERVER_DEADLINE_SEC`.
    """
    settings = get_settings()
    return MailClient(
        base_url=settings.mail_api_base,
        api_key=settings.mail_api_key,
        read_timeout_sec=settings.mail_api_mailserver_timeout_sec,
        deadline_sec=settings.mail_api_mailserver_deadline_sec,
    )


__all__ = [
    "MailClient",
    "MailRejected",
    "MailTimeout",
    "MailUnavailable",
    "get_mail_client",
    "get_mail_server_client",
]
