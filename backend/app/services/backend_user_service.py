"""Бизнес-логика страницы «Пользователи бэков» (modules/backend-users).

CRM — прокси к CRM Admin API бэков (contract v1): собственного хранилища
пользователей бэков нет, admin-ключ расшифровывается в памяти обработчика и
уходит только заголовком `X-Admin-Key` (во frontend не попадает).

Режим «Все приложения»: fan-out по всем бэкам с заданным admin-ключом
(конкурентно, семафор как у монитора), merge отсортированных по `registered_at
DESC` списков и срез [offset, offset+limit). Контракт ограничивает страницу
источника 100 записями, поэтому окно добирается постраничным дочитыванием;
глубина окна ограничена `_MAX_WINDOW` (глубже UI не листает). Упавший источник
не роняет ответ — он попадает в `errors[]` (partial-data warning в UI).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.errors import (
    AppError,
    backend_admin_key_not_set,
    backend_admin_unavailable,
    backend_not_found,
)
from app.infra.backend_admin_client import BackendAdminClient
from app.infra.crypto import decrypt_secret
from app.logging import get_logger
from app.models.service_backend import Backend
from app.repositories.backend_repository import BackendRepository
from app.schemas.backend_user import (
    AddBackendUserTokensRequest,
    BackendProductsResponse,
    BackendUserDetailResponse,
    BackendUserGrantResponse,
    BackendUserItem,
    BackendUserPaymentsResponse,
    BackendUserRequestsResponse,
    BackendUsersListResponse,
    BackendUsersSourceError,
    BackendUsersStats,
    BackendUserTokensResponse,
    GrantBackendUserSubscriptionRequest,
)

logger = get_logger(__name__)

# Максимум одновременных admin-запросов при fan-out (паттерн backend_monitor_service).
_FANOUT_CONCURRENCY = 5

# Страница источника по контракту (§2.1: limit <= 100).
_SOURCE_PAGE_LIMIT = 100

# Предел глубины окна merge-пагинации «Все приложения» (защита от дорогих глубоких страниц).
_MAX_WINDOW = 1000

_CONTRACT_MISMATCH = "Бэк вернул данные не по контракту"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _sort_key(item: BackendUserItem) -> datetime:
    """Ключ merge-сортировки: naive-метки трактуем как UTC (контракт требует UTC)."""
    dt = item.registered_at
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class BackendUserService:
    """Агрегация/транзит CRM Admin API бэков для страницы «Пользователи бэков»."""

    def __init__(self, repository: BackendRepository) -> None:
        self._repo = repository

    # --- список / сводка ---

    async def list_users(
        self,
        *,
        backend_id: uuid.UUID | None,
        search: str | None,
        date_from: str | None,
        date_to: str | None,
        is_paid: bool | None,
        limit: int,
        offset: int,
    ) -> BackendUsersListResponse:
        """Объединённый список пользователей + сводка (04-api.md#get-apibackend-users).

        `backend_id=None` — режим «Все приложения». Окно merge ограничено
        `_MAX_WINDOW` (422 не бросаем — глубже UI не запрашивает, срез вернётся пустым).
        """
        sources = await self._resolve_sources(backend_id)
        if not sources:
            return BackendUsersListResponse(total=0, items=[], stats=BackendUsersStats())

        window = min(offset + limit, _MAX_WINDOW)
        filters = {
            "search": search,
            "date_from": date_from,
            "date_to": date_to,
            "is_paid": is_paid,
        }

        semaphore = asyncio.Semaphore(_FANOUT_CONCURRENCY)
        single_source = backend_id is not None

        async def fetch(backend: Backend, client: BackendAdminClient) -> dict[str, Any]:
            async with semaphore:
                items, total = await self._fetch_window(backend, client, window, filters)
                stats_raw = await client.get_stats(date_from=date_from, date_to=date_to)
                return {"items": items, "total": total, "stats": stats_raw}

        results = await asyncio.gather(*(fetch(b, c) for b, c in sources), return_exceptions=True)

        merged: list[BackendUserItem] = []
        total = 0
        stats = BackendUsersStats()
        errors: list[BackendUsersSourceError] = []
        for (backend, _client), result in zip(sources, results, strict=True):
            if isinstance(result, BaseException):
                # Единственный источник — пробрасываем точную ошибку (404/502 и т.п.).
                if single_source and isinstance(result, AppError):
                    raise result
                if not isinstance(result, Exception):
                    raise result
                errors.append(self._source_error(backend, result))
                continue
            merged.extend(result["items"])
            total += result["total"]
            self._accumulate_stats(stats, backend, result["stats"], errors)

        if stats.users_total > 0:
            stats.cr_percent = round(stats.paid_users / stats.users_total * 100, 1)

        merged.sort(key=_sort_key, reverse=True)
        return BackendUsersListResponse(
            total=total,
            items=merged[offset : offset + limit],
            stats=stats,
            errors=errors,
        )

    async def _fetch_window(
        self,
        backend: Backend,
        client: BackendAdminClient,
        window: int,
        filters: dict[str, Any],
    ) -> tuple[list[BackendUserItem], int]:
        """Дочитывает у источника первые `window` строк страницами по контрактному лимиту."""
        items: list[BackendUserItem] = []
        total = 0
        while len(items) < window:
            page_limit = min(_SOURCE_PAGE_LIMIT, window - len(items))
            raw = await client.list_users(limit=page_limit, offset=len(items), **filters)
            page, total = self._parse_users_page(backend, raw)
            items.extend(page)
            if len(page) < page_limit or len(items) >= total:
                break
        return items, total

    def _parse_users_page(
        self, backend: Backend, raw: dict[str, Any]
    ) -> tuple[list[BackendUserItem], int]:
        raw_items = raw.get("items")
        raw_total = raw.get("total")
        if not isinstance(raw_items, list) or not isinstance(raw_total, int):
            raise backend_admin_unavailable(_CONTRACT_MISMATCH)
        try:
            items = [
                BackendUserItem.model_validate(
                    {**item, "backend_id": backend.id, "backend_name": backend.name}
                )
                for item in raw_items
            ]
        except (ValidationError, TypeError) as exc:
            raise backend_admin_unavailable(_CONTRACT_MISMATCH) from exc
        return items, raw_total

    def _accumulate_stats(
        self,
        acc: BackendUsersStats,
        backend: Backend,
        raw: dict[str, Any],
        errors: list[BackendUsersSourceError],
    ) -> None:
        try:
            stats = BackendUsersStats.model_validate(raw)
        except ValidationError:
            errors.append(
                BackendUsersSourceError(
                    backend_id=backend.id, backend_name=backend.name, message=_CONTRACT_MISMATCH
                )
            )
            return
        acc.users_total += stats.users_total
        acc.paid_users += stats.paid_users
        acc.payments_sum_usd += stats.payments_sum_usd

    @staticmethod
    def _source_error(backend: Backend, exc: Exception) -> BackendUsersSourceError:
        message = exc.message if isinstance(exc, AppError) else "Бэк не ответил на admin-запрос"
        logger.info("backend_users_source_failed", backend_id=str(backend.id), message=message)
        return BackendUsersSourceError(
            backend_id=backend.id, backend_name=backend.name, message=message
        )

    # --- карточка / истории / тарифы ---

    async def get_user(self, backend_id: uuid.UUID, user_id: str) -> BackendUserDetailResponse:
        backend, client = await self._require_source(backend_id)
        raw = await client.get_user(user_id)
        try:
            return BackendUserDetailResponse.model_validate(
                {**raw, "backend_id": backend.id, "backend_name": backend.name}
            )
        except (ValidationError, TypeError) as exc:
            raise backend_admin_unavailable(_CONTRACT_MISMATCH) from exc

    async def list_payments(
        self, backend_id: uuid.UUID, user_id: str, *, limit: int, offset: int
    ) -> BackendUserPaymentsResponse:
        _, client = await self._require_source(backend_id)
        raw = await client.list_payments(user_id, limit=limit, offset=offset)
        return self._validate(BackendUserPaymentsResponse, raw)

    async def list_requests(
        self, backend_id: uuid.UUID, user_id: str, *, limit: int, offset: int
    ) -> BackendUserRequestsResponse:
        _, client = await self._require_source(backend_id)
        raw = await client.list_requests(user_id, limit=limit, offset=offset)
        return self._validate(BackendUserRequestsResponse, raw)

    async def list_products(self, backend_id: uuid.UUID) -> BackendProductsResponse:
        _, client = await self._require_source(backend_id)
        raw = await client.list_products()
        return self._validate(BackendProductsResponse, raw)

    # --- admin-операции (запись) ---

    async def add_tokens(
        self, backend_id: uuid.UUID, user_id: str, payload: AddBackendUserTokensRequest
    ) -> BackendUserTokensResponse:
        """Начисление/списание токенов. НЕ идемпотентно (контракт §3.1) — защита от
        двойного сабмита лежит на UI; сервис лишь транзитом передаёт сумму."""
        _, client = await self._require_source(backend_id)
        raw = await client.add_tokens(user_id, amount=payload.amount)
        return self._validate(BackendUserTokensResponse, raw)

    async def grant_subscription(
        self,
        backend_id: uuid.UUID,
        user_id: str,
        payload: GrantBackendUserSubscriptionRequest,
    ) -> BackendUserGrantResponse:
        """Выдача/продление плана. Идемпотентна по `grant_id` (контракт §3.2)."""
        _, client = await self._require_source(backend_id)
        raw = await client.grant_subscription(
            user_id,
            product_id=payload.product_id,
            expires_in_days=payload.expires_in_days,
            grant_id=payload.grant_id,
        )
        return self._validate(BackendUserGrantResponse, raw)

    # --- источники ---

    async def _resolve_sources(
        self, backend_id: uuid.UUID | None
    ) -> list[tuple[Backend, BackendAdminClient]]:
        """Источники агрегации: один бэк (обязан иметь admin-ключ) или все с ключом."""
        if backend_id is not None:
            return [await self._require_source(backend_id)]
        backends = await self._repo.list_all()
        return [(b, self._client(b)) for b in backends if b.admin_api_key_encrypted is not None]

    async def _require_source(self, backend_id: uuid.UUID) -> tuple[Backend, BackendAdminClient]:
        backend = await self._repo.get_by_id(backend_id)
        if backend is None:
            raise backend_not_found()
        if backend.admin_api_key_encrypted is None:
            raise backend_admin_key_not_set()
        return backend, self._client(backend)

    @staticmethod
    def _client(backend: Backend) -> BackendAdminClient:
        encrypted = backend.admin_api_key_encrypted
        if encrypted is None:  # защищено фильтрами _resolve_sources/_require_source
            raise backend_admin_key_not_set()
        return BackendAdminClient(
            backend_id=backend.id,
            domain=backend.domain,
            admin_key=decrypt_secret(encrypted),
        )

    @staticmethod
    def _validate(schema: type[_ModelT], raw: dict[str, Any]) -> _ModelT:
        try:
            return schema.model_validate(raw)
        except (ValidationError, TypeError) as exc:
            raise backend_admin_unavailable(_CONTRACT_MISMATCH) from exc
