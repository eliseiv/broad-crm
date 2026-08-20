"""HTTP-клиент универсального CRM Admin API бэков (contract v1 + расширение v1.1).

Контракт зафиксирован вне репозитория (BA/crm-admin-api-contract.txt) и в
docs/modules/backend-users/README.md (v1) + docs/modules/backend-economics/README.md
(расширение v1.1 «экономика», ADR-072): бэк отдаёт admin-эндпоинты под заголовком
`X-Admin-Key` с ОДНИМ из двух префиксов — `/api/billing/admin` или `/v1/admin`.

**Детекция префикса идёт ВСЕГДА по v1-пути `GET {P}/products` и только по нему**
(ADR-072 §4а, инвариант 4 §1): этот путь обязан работать и на бэке без расширения.
Расширенные пути v1.1 (`/pricing`, `PATCH`, `/capabilities`) вызываются только
против УЖЕ определённого префикса — иначе первый же вызов расширения при холодном
кэше даёт 404 на обоих кандидатах, CRM объявляет бэк не реализующим контракт вовсе
и префикс не кэшируется (v1-функции страницы «Юзеры бэков» деградируют).

**Семантика 404 задаётся вызывающим явно и БЕЗ значения по умолчанию** (ADR-072 §4б):
`NotFoundSemantics.CONTRACT` → 502 backend_admin_not_supported; `USER` (только пути
`/users/*`) → 404 backend_user_not_found; `EXTENSION` (расширенные пути v1.1) →
502 backend_admin_extension_not_supported. Отсутствие дефолта — гейт: каждый новый
метод обязан решить вопрос сам, иначе копипаст воспроизводит ложное «Пользователь не
найден» на странице, где пользователя нет вообще.

Прочие ошибки: сеть/таймаут/5xx → 502 backend_admin_unavailable; 401/403 → 502
backend_admin_rejected (неверный ключ); 400 → backend_admin_bad_request (текст detail
бэка); 409 → 409 backend_admin_conflict (конфликт `if_updated_at`, ADR-072 §4в).

`X-Admin-Actor: crm:<user_uuid>` уходит на PATCH — это **ЗАЯВЛЕНИЕ, А НЕ
АУТЕНТИФИКАЦИЯ** (ADR-072 §9): значение ничем не подтверждается и не проверяется
бэком, оно пригодно для корреляции в логах и НЕПРИГОДНО как основание отчёта «кто
менял». Единственная аутентификация — `X-Admin-Key`; он передаётся только заголовком
и не логируется (в том числе не попадает в события деградации).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.errors import (
    AppError,
    backend_admin_bad_request,
    backend_admin_conflict,
    backend_admin_extension_not_supported,
    backend_admin_not_supported,
    backend_admin_rejected,
    backend_admin_response_unusable,
    backend_admin_unavailable,
    backend_admin_upstream_status,
    backend_user_not_found,
)
from app.logging import get_logger

logger = get_logger(__name__)

# Варианты префикса контракта (порядок = порядок детекции).
PREFIX_CANDIDATES: tuple[str, ...] = ("/api/billing/admin", "/v1/admin")

ADMIN_KEY_HEADER = "X-Admin-Key"
ADMIN_ACTOR_HEADER = "X-Admin-Actor"

# Максимальная длина `X-Admin-Actor` по контракту (ADR-072 §1, инвариант 8).
_ACTOR_MAX_LENGTH = 255

# Единственный путь детекции префикса — v1-path, обязательный и без расширения.
PROBE_PATH = "/products"

# Нормативный фолбэк текста причины `400`/`422`, когда `detail` неизвлекаем
# (ADR-072 §7.3, 04-api.md#backend-economics). Называет причину и действие — в
# отличие от номера статуса.
_VALIDATION_REJECTED_FALLBACK = "Бэк отверг значение: не прошло проверку на стороне бэка"

# Кэш определённого префикса по id бэка (in-memory, процесс-локальный).
_prefix_cache: dict[uuid.UUID, str] = {}


def _clear_prefix_cache() -> None:
    """Сброс кэша префиксов (для тестов)."""
    _prefix_cache.clear()


def _segment(value: str) -> str:
    """Экранирует идентификатор, подставляемый в ПУТЬ upstream-запроса.

    Идентификаторы (`product_id`, `tariff_id`, `user_id`) приходят сырой строкой из
    path-параметра CRM и по контракту **opaque** — интерпретировать их CRM не вправе.
    Без экранирования значение вида `../users/<id>` меняет сам путь: httpx нормализует
    `..` ДО отправки (проверено — запрос уходит на `/users/<id>`), то есть держатель
    `backend-economics:edit` направил бы PATCH с admin-ключом CRM на произвольный
    admin-путь бэка. `safe=""` экранирует и `/`, оставляя ровно ОДИН сегмент пути.
    """
    return quote(value, safe="")


class NotFoundSemantics(Enum):
    """Смысл 404 от бэка — задаётся вызывающим явно (ADR-072 §4б, без дефолта).

    `CONTRACT` — v1-путь вне `/users/*` (в т.ч. probe детекции): контракт не
    реализован. `USER` — пути `/users/*`: пользователя нет. `EXTENSION` —
    расширенные пути v1.1: бэк остался на уровне v1.
    """

    CONTRACT = "contract"
    USER = "user"
    EXTENSION = "extension"


# --- Машинные причины неуспеха подзапроса `/capabilities` (ADR-072 §7.1) ---
#
# Перечень ЗАКРЫТ и обязан покрывать ВСЕ исходы: новый исход добавляется СТРОКОЙ в
# таблицу ADR-072 §7.1, а не относится к ближайшему по смыслу. `reason` существует
# ради разбора инцидента — DNS-сбой, помеченный `http_5xx`, послал бы дежурного искать
# ошибку на стороне бэка, которого запрос даже не достиг.

REASON_NOT_FOUND = "not_found"
REASON_TIMEOUT = "timeout"
REASON_TRANSPORT = "transport"
REASON_REDIRECT = "redirect"
REASON_REJECTED = "rejected"
REASON_HTTP_4XX = "http_4xx"
REASON_HTTP_5XX = "http_5xx"
REASON_BAD_JSON = "bad_json"
REASON_SCHEMA_MISMATCH = "schema_mismatch"


class BackendAdminUpstreamError(Exception):
    """Сбой admin-запроса с МАШИННОЙ причиной и готовой доменной ошибкой.

    Причина (`reason`) нужна вызывающему, который обязан различать исходы без разбора
    текста сообщения: подзапрос `/capabilities` пишет её в лог событием
    `backend_admin_capabilities_unavailable` (ADR-072 §7.1). Наружу из клиента эта
    ошибка не выходит — публичные методы поднимают `AppError` (`.error`).
    """

    def __init__(self, reason: str, error: AppError) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error = error


@dataclass(frozen=True, slots=True)
class CapabilitiesResult:
    """Исход подзапроса `/capabilities`: либо тело, либо машинная причина неуспеха.

    Метод не бросает: ЛЮБОЙ неуспех необязательного подзапроса обязан давать
    `capabilities: null` при отданном списке (ADR-072 §7.1), поэтому исход
    возвращается значением, а не исключением.
    """

    data: dict[str, Any] | None
    reason: str | None


class BackendAdminClient:
    """Клиент admin-эндпоинтов ОДНОГО бэка (domain — канон `https://<host>/`)."""

    def __init__(self, backend_id: uuid.UUID, domain: str, admin_key: str) -> None:
        self._backend_id = backend_id
        # Канон домена заканчивается «/», префиксы начинаются с «/» — убираем дубль.
        self._base = domain.rstrip("/")
        self._admin_key = admin_key

    # --- contract v1 ---

    async def list_users(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        is_paid: bool | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if search:
            params["search"] = search
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        if is_paid is not None:
            params["is_paid"] = is_paid
        return await self._get("/users", params=params, not_found=NotFoundSemantics.CONTRACT)

    async def get_user(self, user_id: str) -> dict[str, Any]:
        return await self._get(f"/users/{_segment(user_id)}", not_found=NotFoundSemantics.USER)

    async def list_payments(self, user_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        return await self._get(
            f"/users/{_segment(user_id)}/payments",
            params={"limit": limit, "offset": offset},
            not_found=NotFoundSemantics.USER,
        )

    async def list_requests(self, user_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        return await self._get(
            f"/users/{_segment(user_id)}/requests",
            params={"limit": limit, "offset": offset},
            not_found=NotFoundSemantics.USER,
        )

    async def get_stats(
        self, *, date_from: str | None = None, date_to: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        return await self._get("/stats", params=params, not_found=NotFoundSemantics.CONTRACT)

    async def list_products(self, *, scope: str | None = None) -> dict[str, Any]:
        """Каталог продуктов (v1-путь, он же probe детекции префикса).

        `scope=None` — параметр НЕ отправляется: у бэка действует умолчание
        `grantable`, чем сохраняется поведение формы «Установить план» страницы
        «Юзеры бэков» (04-api.md#backend-users). Полный каталог (`scope="all"`)
        читает страница «Продукты и тарифы».
        """
        params = {"scope": scope} if scope is not None else None
        return await self._get(PROBE_PATH, params=params, not_found=NotFoundSemantics.CONTRACT)

    async def add_tokens(self, user_id: str, *, amount: int) -> dict[str, Any]:
        return await self._post(
            f"/users/{_segment(user_id)}/tokens",
            body={"amount": amount},
            not_found=NotFoundSemantics.USER,
        )

    async def grant_subscription(
        self, user_id: str, *, product_id: str, expires_in_days: int, grant_id: str
    ) -> dict[str, Any]:
        return await self._post(
            f"/users/{_segment(user_id)}/subscription",
            body={
                "product_id": product_id,
                "expires_in_days": expires_in_days,
                "grant_id": grant_id,
            },
            not_found=NotFoundSemantics.USER,
        )

    # --- расширение v1.1 «экономика» (ADR-072 §1) ---

    async def update_product(
        self, product_id: str, *, body: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        """PATCH токенов продукта. Строк не создаёт: неизвестный `product_id` → 400 бэка."""
        return await self._patch(
            f"/products/{_segment(product_id)}",
            body=body,
            actor=actor,
            not_found=NotFoundSemantics.EXTENSION,
        )

    async def list_pricing(self) -> dict[str, Any]:
        """Тарифы списания за генерацию (путь существует ТОЛЬКО в v1.1)."""
        return await self._get("/pricing", not_found=NotFoundSemantics.EXTENSION)

    async def update_pricing(
        self, tariff_id: str, *, body: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        """PATCH тарифа списания. `tariff_id` opaque для CRM — только ключ пути."""
        return await self._patch(
            f"/pricing/{_segment(tariff_id)}",
            body=body,
            actor=actor,
            not_found=NotFoundSemantics.EXTENSION,
        )

    async def get_capabilities(self) -> CapabilitiesResult:
        """Подзапрос `/capabilities` — НЕ бросает (ADR-072 §7.1).

        ЛЮБОЙ неуспех (404, таймаут, 5xx, 401/403, битый JSON) возвращается машинной
        причиной: вызывающий обязан отдать список с `capabilities: null`, а не
        провалить его в 502 из-за необязательного подзапроса.
        """
        try:
            data = await self._request(
                "GET",
                "/capabilities",
                params=None,
                body=None,
                not_found=NotFoundSemantics.EXTENSION,
                actor=None,
            )
        except BackendAdminUpstreamError as exc:
            return CapabilitiesResult(data=None, reason=exc.reason)
        return CapabilitiesResult(data=data, reason=None)

    # --- внутреннее ---

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        not_found: NotFoundSemantics,
    ) -> dict[str, Any]:
        return await self._call("GET", path, params=params, body=None, not_found=not_found)

    async def _post(
        self, path: str, *, body: dict[str, Any], not_found: NotFoundSemantics
    ) -> dict[str, Any]:
        return await self._call("POST", path, params=None, body=body, not_found=not_found)

    async def _patch(
        self,
        path: str,
        *,
        body: dict[str, Any],
        actor: str,
        not_found: NotFoundSemantics,
    ) -> dict[str, Any]:
        return await self._call(
            "PATCH", path, params=None, body=body, not_found=not_found, actor=actor
        )

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        not_found: NotFoundSemantics,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Публичная граница: машинная причина сбоя превращается в доменную ошибку."""
        try:
            return await self._request(
                method, path, params=params, body=body, not_found=not_found, actor=actor
            )
        except BackendAdminUpstreamError as exc:
            raise exc.error from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        not_found: NotFoundSemantics,
        actor: str | None,
    ) -> dict[str, Any]:
        """Запрос против определённого префикса (детекция — отдельным v1-probe).

        Если префикс ещё не известен, он определяется probe-запросом `GET {P}/products`
        (ADR-072 §4а) — кроме случая, когда сам вызов и есть этот probe: тогда детекция
        выполняется им же, без лишнего round-trip'а.
        """
        is_probe = method == "GET" and path == PROBE_PATH
        prefix = _prefix_cache.get(self._backend_id)
        if prefix is None and not is_probe:
            prefix = await self._detect_prefix()

        candidates = PREFIX_CANDIDATES if prefix is None else (prefix,)
        detecting = prefix is None

        async with self._client() as client:
            for index, candidate in enumerate(candidates):
                response = await self._send(
                    client, method, f"{self._base}{candidate}{path}", params, body, actor
                )
                if response.status_code == 404:
                    if detecting and index < len(candidates) - 1:
                        # Детекция: 404 = «префикс не тот», пробуем следующий кандидат.
                        continue
                    raise self._not_found_error(not_found, detecting=detecting)
                self._remember_prefix(candidate)
                return self._parse(response)

        raise self._upstream(  # недостижимо: цикл завершается raise/return
            REASON_NOT_FOUND, backend_admin_not_supported()
        )

    async def _detect_prefix(self) -> str:
        """Определяет префикс v1-probe'ом `GET {P}/products` и кэширует его.

        Probe идёт по пути, который обязан существовать и на бэке уровня v1
        (ADR-072 §1 инвариант 4), поэтому вызов расширения при холодном кэше не
        объявляет контракт нереализованным.
        """
        await self._request(
            "GET",
            PROBE_PATH,
            params=None,
            body=None,
            not_found=NotFoundSemantics.CONTRACT,
            actor=None,
        )
        prefix = _prefix_cache.get(self._backend_id)
        if prefix is None:  # успешный probe всегда кэширует префикс
            raise self._upstream(REASON_NOT_FOUND, backend_admin_not_supported())
        return prefix

    def _client(self) -> httpx.AsyncClient:
        timeout = get_settings().backend_check_timeout_sec
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
            verify=True,
            follow_redirects=False,
            headers={ADMIN_KEY_HEADER: self._admin_key},
        )

    async def _send(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        actor: str | None,
    ) -> httpx.Response:
        headers = {ADMIN_ACTOR_HEADER: actor[:_ACTOR_MAX_LENGTH]} if actor else None
        try:
            return await client.request(method, url, params=params, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise self._upstream(
                REASON_TIMEOUT, backend_admin_unavailable("Таймаут admin-запроса к бэку")
            ) from exc
        except httpx.HTTPError as exc:
            # Сбой транспорта БЕЗ ответа (DNS/отказ соединения/TLS) — отдельная причина
            # `transport` (ADR-072 §7.1): запрос не достиг бэка, и путать это с ошибкой
            # на его стороне нельзя. Это НЕ таймаут — тот перехвачен веткой выше.
            raise self._upstream(REASON_TRANSPORT, backend_admin_unavailable()) from exc

    def _not_found_error(
        self, semantics: NotFoundSemantics, *, detecting: bool
    ) -> BackendAdminUpstreamError:
        """404 → ошибка по ЯВНО заданной вызывающим семантике (ADR-072 §4б).

        При детекции (оба кандидата ответили 404) смысл один — контракт не реализован
        вовсе, независимо от семантики вызывающего.
        """
        if detecting or semantics is NotFoundSemantics.CONTRACT:
            return self._upstream(REASON_NOT_FOUND, backend_admin_not_supported())
        if semantics is NotFoundSemantics.USER:
            return self._upstream(REASON_NOT_FOUND, backend_user_not_found())
        return self._upstream(REASON_NOT_FOUND, backend_admin_extension_not_supported())

    def _remember_prefix(self, prefix: str) -> None:
        if _prefix_cache.get(self._backend_id) != prefix:
            _prefix_cache[self._backend_id] = prefix
            logger.info(
                "backend_admin_prefix_detected",
                backend_id=str(self._backend_id),
                prefix=prefix,
            )

    def _parse(self, response: httpx.Response) -> dict[str, Any]:
        status_code = response.status_code
        if status_code in (401, 403):
            raise self._upstream(REASON_REJECTED, backend_admin_rejected())
        # 400 И 422 — ОДИН код CRM (ADR-072 §7.3): для оператора это один класс «бэк
        # отверг введённое значение — исправьте ввод». У контрагента 422 = отказ
        # валидации тела (границы/точность), 400 = неизвестный id. Путь штатный:
        # верхних границ в схемах запроса CRM нет намеренно, поэтому при отсутствии
        # `limits` значение доходит до бэка и отвергается им. Оставить 422 в общей
        # ветке не-2xx нельзя — там он стал бы 502 с голым «Ошибка бэка (HTTP 422)».
        if status_code in (400, 422):
            raise self._upstream(REASON_HTTP_4XX, backend_admin_bad_request(self._detail(response)))
        if status_code == 409:
            raise self._upstream(REASON_HTTP_4XX, backend_admin_conflict())
        if 200 <= status_code < 300:
            # Ниже — исходы, где бэк УЖЕ ПОДТВЕРДИЛ операцию статусом 2xx, и негодно
            # лишь тело. Тип ошибки отличимый (`BackendAdminResponseUnusable`), чтобы
            # путь записи успел зафиксировать состоявшийся факт в аудите до её
            # проброса (ADR-073 §8.3). Код контракта тот же — `502
            # backend_admin_unavailable`, читающие пути не меняются.
            try:
                data = response.json()
            except ValueError as exc:
                raise self._upstream(
                    REASON_BAD_JSON, backend_admin_response_unusable("Бэк вернул невалидный JSON")
                ) from exc
            if not isinstance(data, dict):
                # Тело РАЗОБРАЛОСЬ как JSON (`[]`, `"ok"`, `5`), но не соответствует
                # схеме — это `schema_mismatch`, а НЕ `bad_json` (ADR-072 §7.1:
                # `bad_json` = «тело не разбирается как JSON»).
                raise self._upstream(
                    REASON_SCHEMA_MISMATCH,
                    backend_admin_response_unusable("Бэк вернул неожиданный формат ответа"),
                )
            return data
        # Статус источника сохраняется МАШИННО (`BackendAdminUpstreamStatus`), а не только
        # в тексте: фоновый воркер снимка обязан отличать временный отказ (`429`/`5xx`,
        # лечится backoff'ом) от постоянного, не разбирая формулировку сообщения.
        # Код/статус/текст контракта прежние — интерактивные пути не меняются.
        raise self._upstream(
            self._status_reason(status_code),
            backend_admin_upstream_status(status_code, self._retry_after(response)),
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        """`Retry-After` в секундах, если бэк его отдал в числовой форме.

        HTTP-date форма заголовка сознательно НЕ разбирается: она требует доверия к часам
        источника и к его часовому поясу, а промах здесь дороже пользы — вызывающий и без
        заголовка имеет свой exponential backoff. Отрицательное/нечисловое значение — то же
        «заголовка нет».
        """
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            seconds = float(raw.strip())
        except ValueError:
            return None
        return seconds if seconds > 0 else None

    @staticmethod
    def _status_reason(status_code: int) -> str:
        """Причина по КЛАССУ статуса ответа (ADR-072 §7.1 — перечень закрыт и полон).

        `3xx` — `redirect`: клиент ходит с `follow_redirects=False`, поэтому редирект
        достижим и означает ошибку КОНФИГУРАЦИИ АДРЕСА (http→https, канонический хост,
        слеш), а не сбой бэка. `401`/`403` (`rejected`) и `404` (`not_found`) разобраны
        отдельными ветками выше, поэтому `4xx` здесь — «прочие `4xx`»; `5xx` — сбой на
        стороне бэка.

        Ветки «прочий неопознанный исход → `http_5xx`» здесь НЕТ намеренно: сваливание
        неопознанного к ближайшему по смыслу — дефект (§7.1), новый класс исхода
        добавляется строкой в таблицу ADR. Три ветки покрывают ровно то, что сюда
        доходит: `2xx` разобран веткой тела выше, исход без ответа — `timeout`/
        `transport` (`_send`), `1xx` httpx финальным ответом не отдаёт, а статус вне
        допустимого диапазона отвергается на уровне протокола (`RemoteProtocolError`
        — подкласс `httpx.HTTPError`, то есть тот же `transport`).
        """
        if 300 <= status_code < 400:
            return REASON_REDIRECT
        if 400 <= status_code < 500:
            return REASON_HTTP_4XX
        return REASON_HTTP_5XX

    @staticmethod
    def _upstream(reason: str, error: AppError) -> BackendAdminUpstreamError:
        return BackendAdminUpstreamError(reason, error)

    @classmethod
    def _detail(cls, response: httpx.Response) -> str:
        """Человекочитаемая причина отказа из тела `400`/`422` бэка (ADR-072 §7.3).

        Форматы контрагента различаются: у `400` `detail` — строка, у `422` (FastAPI) —
        СПИСОК объектов `{loc, msg, type}`. Норма: строка идёт транзитом; из списка
        берётся `msg` первого элемента с указанием поля из хвоста `loc`; если извлечь
        текст не удалось — нормативный фолбэк, который всё равно называет причину и
        действие, в отличие от номера статуса.
        """
        try:
            data = response.json()
        except ValueError:
            return _VALIDATION_REJECTED_FALLBACK
        detail = data.get("detail") if isinstance(data, dict) else None
        if isinstance(detail, str) and detail:
            return detail
        if isinstance(detail, list) and detail:
            return cls._detail_from_items(detail)
        return _VALIDATION_REJECTED_FALLBACK

    @staticmethod
    def _detail_from_items(items: list[Any]) -> str:
        """`msg` первого элемента списка `detail` (+ поле из хвоста `loc`)."""
        first = items[0]
        if not isinstance(first, dict):
            return _VALIDATION_REJECTED_FALLBACK
        message = first.get("msg")
        if not isinstance(message, str) or not message:
            return _VALIDATION_REJECTED_FALLBACK
        loc = first.get("loc")
        if isinstance(loc, list):
            # Хвост `loc` — имя поля (`["body", "tokens"]`); индексы элементов пропускаем.
            fields = [part for part in loc if isinstance(part, str)]
            if fields:
                return f"{fields[-1]}: {message}"
        return message


__all__ = [
    "ADMIN_ACTOR_HEADER",
    "ADMIN_KEY_HEADER",
    "PREFIX_CANDIDATES",
    "PROBE_PATH",
    "REASON_BAD_JSON",
    "REASON_HTTP_4XX",
    "REASON_HTTP_5XX",
    "REASON_NOT_FOUND",
    "REASON_REDIRECT",
    "REASON_REJECTED",
    "REASON_SCHEMA_MISMATCH",
    "REASON_TIMEOUT",
    "REASON_TRANSPORT",
    "BackendAdminClient",
    "BackendAdminUpstreamError",
    "CapabilitiesResult",
    "NotFoundSemantics",
    "_clear_prefix_cache",
]
