"""HTTP-клиент универсального CRM Admin API бэков (contract v1, modules/backend-users).

Контракт зафиксирован вне репозитория (BA/crm-admin-api-contract.txt) и в
docs/modules/backend-users/README.md: бэк отдаёт admin-эндпоинты под заголовком
`X-Admin-Key` с ОДНИМ из двух префиксов — `/api/billing/admin` или `/v1/admin`.
Клиент определяет префикс автоматически: пробует кандидатов по порядку; 404 на
GET-пути трактуется как «префикс не тот» и пробуется следующий; рабочий префикс
кэшируется в памяти процесса по id бэка (сбрасывается рестартом — повторная
детекция дешёвая). Оба кандидата 404 → бэк контракт не реализует.

Ошибки транслируются в AppError: сеть/таймаут/5xx → 502 backend_admin_unavailable;
401/403 → 502 backend_admin_rejected (неверный ключ); 404 внутри рабочего префикса →
404 backend_user_not_found; 400 → backend_admin_bad_request (текст detail бэка).
Admin-ключ передаётся только заголовком и не логируется.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from app.config import get_settings
from app.errors import (
    backend_admin_bad_request,
    backend_admin_not_supported,
    backend_admin_rejected,
    backend_admin_unavailable,
    backend_user_not_found,
)
from app.logging import get_logger

logger = get_logger(__name__)

# Варианты префикса контракта (порядок = порядок детекции).
PREFIX_CANDIDATES: tuple[str, ...] = ("/api/billing/admin", "/v1/admin")

ADMIN_KEY_HEADER = "X-Admin-Key"

# Кэш определённого префикса по id бэка (in-memory, процесс-локальный).
_prefix_cache: dict[uuid.UUID, str] = {}


def _clear_prefix_cache() -> None:
    """Сброс кэша префиксов (для тестов)."""
    _prefix_cache.clear()


class BackendAdminClient:
    """Клиент admin-эндпоинтов ОДНОГО бэка (domain — канон `https://<host>/`)."""

    def __init__(self, backend_id: uuid.UUID, domain: str, admin_key: str) -> None:
        self._backend_id = backend_id
        # Канон домена заканчивается «/», префиксы начинаются с «/» — убираем дубль.
        self._base = domain.rstrip("/")
        self._admin_key = admin_key

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
        return await self._get("/users", params=params)

    async def get_user(self, user_id: str) -> dict[str, Any]:
        return await self._get(f"/users/{user_id}", not_found_is_user=True)

    async def list_payments(self, user_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        return await self._get(
            f"/users/{user_id}/payments",
            params={"limit": limit, "offset": offset},
            not_found_is_user=True,
        )

    async def list_requests(self, user_id: str, *, limit: int, offset: int) -> dict[str, Any]:
        return await self._get(
            f"/users/{user_id}/requests",
            params={"limit": limit, "offset": offset},
            not_found_is_user=True,
        )

    async def get_stats(
        self, *, date_from: str | None = None, date_to: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if date_from:
            params["date_from"] = date_from
        if date_to:
            params["date_to"] = date_to
        return await self._get("/stats", params=params)

    async def list_products(self) -> dict[str, Any]:
        return await self._get("/products")

    async def add_tokens(self, user_id: str, *, amount: int) -> dict[str, Any]:
        return await self._post(f"/users/{user_id}/tokens", body={"amount": amount})

    async def grant_subscription(
        self, user_id: str, *, product_id: str, expires_in_days: int, grant_id: str
    ) -> dict[str, Any]:
        return await self._post(
            f"/users/{user_id}/subscription",
            body={
                "product_id": product_id,
                "expires_in_days": expires_in_days,
                "grant_id": grant_id,
            },
        )

    # --- внутреннее ---

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        not_found_is_user: bool = False,
    ) -> dict[str, Any]:
        return await self._request(
            "GET", path, params=params, body=None, not_found_is_user=not_found_is_user
        )

    async def _post(self, path: str, *, body: dict[str, Any]) -> dict[str, Any]:
        # 404 на POST /users/{id}/... при уже известном префиксе — «пользователь не найден».
        return await self._request("POST", path, params=None, body=body, not_found_is_user=True)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: dict[str, Any] | None,
        not_found_is_user: bool,
    ) -> dict[str, Any]:
        """Запрос с автоопределением префикса (404 на кандидате → следующий кандидат).

        Если префикс уже известен (кэш), 404 означает «ресурс не найден» и, для путей
        с user_id, транслируется в `backend_user_not_found` — иначе контракт-404.
        """
        timeout = get_settings().backend_check_timeout_sec
        cached = _prefix_cache.get(self._backend_id)
        candidates = (cached,) if cached is not None else PREFIX_CANDIDATES

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
            verify=True,
            follow_redirects=False,
            headers={ADMIN_KEY_HEADER: self._admin_key},
        ) as client:
            for index, prefix in enumerate(candidates):
                url = f"{self._base}{prefix}{path}"
                try:
                    response = await client.request(method, url, params=params, json=body)
                except httpx.TimeoutException as exc:
                    raise backend_admin_unavailable("Таймаут admin-запроса к бэку") from exc
                except httpx.HTTPError as exc:
                    raise backend_admin_unavailable() from exc

                if response.status_code == 404:
                    # Кэшированный префикс: 404 = ресурс, не контракт.
                    if cached is not None:
                        if not_found_is_user:
                            raise backend_user_not_found()
                        raise backend_admin_not_supported()
                    # Детекция: пробуем следующий кандидат.
                    if index < len(candidates) - 1:
                        continue
                    raise backend_admin_not_supported()

                self._remember_prefix(prefix)
                return self._parse(response)

        raise backend_admin_not_supported()  # недостижимо: цикл завершается raise/return

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
            raise backend_admin_rejected()
        if status_code == 400:
            raise backend_admin_bad_request(self._detail(response))
        if 200 <= status_code < 300:
            try:
                data = response.json()
            except ValueError as exc:
                raise backend_admin_unavailable("Бэк вернул невалидный JSON") from exc
            if not isinstance(data, dict):
                raise backend_admin_unavailable("Бэк вернул неожиданный формат ответа")
            return data
        raise backend_admin_unavailable(f"Ошибка бэка (HTTP {status_code})")

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        """Достаёт человекочитаемый `detail` из тела 400 бэка (фолбэк — общая фраза)."""
        try:
            data = response.json()
            detail = data.get("detail") if isinstance(data, dict) else None
            if isinstance(detail, str) and detail:
                return detail
        except ValueError:
            pass
        return "Бэк отверг операцию"


__all__ = [
    "ADMIN_KEY_HEADER",
    "PREFIX_CANDIDATES",
    "BackendAdminClient",
    "_clear_prefix_cache",
]
