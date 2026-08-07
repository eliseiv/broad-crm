"""Бизнес-логика страницы «Продукты и тарифы» (modules/backend-economics, ADR-072).

CRM — **прокси без собственного хранилища** (ADR-069 §3 подтверждён, ADR-072 §3):
продукты, тарифы и себестоимость не копируются в БД CRM — читаются на лету и пишутся
напрямую в бэк. Причина сильнее, чем у backend-users: продукт и тариф — денежные
величины, у которых недопустимо два дома.

Ключевые нормы, реализованные здесь:
- `capabilities` приходят **конвертом** вместе со списком (атомарность снимка: список
  и его write-аффорданс — один ответ). В пределах ОДНОГО обработчика подзапрос идёт
  не более раза; межзапросного кэша фич нет (ADR-072 §7.2);
- **ЛЮБОЙ неуспех подзапроса `/capabilities`** ⇒ `capabilities: null` + список
  отдаётся 200; причина пишется событием `backend_admin_capabilities_unavailable`
  (ADR-072 §7.1). Провалить список в 502 из-за необязательного подзапроса запрещено;
- списки **не пагинируются**; ответ с > 500 элементами отвергается как contract
  mismatch (ADR-072 §1 инвариант 7);
- аудит правки пишется вызывающим (роутер) **только после успеха бэка** (ADR-072 §10).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.errors import BackendAdminResponseUnusable, backend_admin_unavailable
from app.infra.backend_admin_client import (
    REASON_SCHEMA_MISMATCH,
    BackendAdminClient,
)
from app.logging import get_logger
from app.models.service_backend import Backend
from app.repositories.backend_repository import BackendRepository
from app.schemas.backend_economics import (
    BackendEconomicsBackendItem,
    BackendEconomicsBackendsResponse,
    BackendEconomicsCapabilities,
    BackendEconomicsPricingResponse,
    BackendEconomicsProduct,
    BackendEconomicsProductsResponse,
    BackendEconomicsTariff,
    BackendProductUpdateResponse,
    BackendTariffUpdateResponse,
    UpdateBackendProductRequest,
    UpdateBackendTariffRequest,
)
from app.services.backend_admin_source import BackendAdminSourceResolver

logger = get_logger(__name__)

_ModelT = TypeVar("_ModelT", bound=BaseModel)

_CONTRACT_MISMATCH = "Бэк вернул данные не по контракту"

# Списки каталога не пагинируются; больший ответ = не-контрактные данные (ADR-072 §1.7).
_MAX_ITEMS = 500

# Значение `X-Admin-Actor` — ЗАЯВЛЕНИЕ, а не аутентификация (ADR-072 §9): бэк его не
# проверяет, оно непригодно как основание отчёта «кто менял».
_ACTOR_PREFIX = "crm:"

# Полный каталог продуктов (в отличие от формы «Установить план», где `scope` не шлётся
# и действует умолчание `grantable`).
_PRODUCTS_SCOPE_ALL = "all"


class BackendEconomicsService:
    """Транзит расширения v1.1 «экономика» CRM Admin API бэков."""

    def __init__(self, repository: BackendRepository) -> None:
        self._repo = repository
        # Расшифровка admin-ключа — общий security-critical путь двух модулей.
        self._sources = BackendAdminSourceResolver(repository)

    # --- селектор приложения ---

    async def list_backends(self) -> BackendEconomicsBackendsResponse:
        """Бэки с заданным Admin API Key (гейт — `backend-economics:view`, не `backends:view`).

        Селектор страницы не должен зависеть от чужого права: режима «Все приложения»
        здесь нет, и без селектора страница нерабочая.
        """
        backends = await self._repo.list_all()
        items = [
            BackendEconomicsBackendItem(id=b.id, code=b.code, name=b.name)
            for b in sorted(
                (b for b in backends if b.admin_api_key_encrypted is not None),
                key=lambda b: (b.name, b.code),
            )
        ]
        return BackendEconomicsBackendsResponse(items=items)

    # --- чтение каталогов ---

    async def list_products(self, backend_id: uuid.UUID) -> BackendEconomicsProductsResponse:
        """Полный каталог продуктов (`scope=all`) + конверт `capabilities`."""
        backend, client = await self._sources.require(backend_id)
        raw = await client.list_products(scope=_PRODUCTS_SCOPE_ALL)
        items = self._parse_items(raw, BackendEconomicsProduct)
        return BackendEconomicsProductsResponse(
            items=items,
            capabilities=await self._fetch_capabilities(backend, client),
        )

    async def list_pricing(self, backend_id: uuid.UUID) -> BackendEconomicsPricingResponse:
        """Тарифы списания + конверт `capabilities`.

        Путь существует только в v1.1: у бэка уровня v1 клиент отдаст
        `backend_admin_extension_not_supported` (информационное состояние, не ошибка).
        """
        backend, client = await self._sources.require(backend_id)
        raw = await client.list_pricing()
        items = self._parse_items(raw, BackendEconomicsTariff)
        return BackendEconomicsPricingResponse(
            items=items,
            capabilities=await self._fetch_capabilities(backend, client),
        )

    # --- правка (PATCH идемпотентен: устанавливает значение, а не дельту) ---

    async def update_product(
        self,
        backend_id: uuid.UUID,
        product_id: str,
        payload: UpdateBackendProductRequest,
        *,
        actor_id: uuid.UUID,
        on_applied: Callable[[BackendProductUpdateResponse | None], None],
    ) -> BackendProductUpdateResponse:
        """Правка продукта. Строк не создаёт: неизвестный `product_id` → 400 бэка.

        `on_applied` вызывается РОВНО ОДИН РАЗ сразу после подтверждения бэка и ДО
        строгого разбора ответа (ADR-073 §8.3): аудит фиксирует СОСТОЯВШИЙСЯ факт на
        стороне бэка, а не качество его ответа. Порядок «исключение до аудита»
        превращал бы неполный ответ в необратимую потерю следа: признак у бэка уже
        переключён, оператор видит красную ошибку, аудит молчит. Аргумент —
        толерантно разобранный ответ или `None`, если он не разбирается вовсе.
        """
        _, client = await self._sources.require(backend_id)
        try:
            raw = await client.update_product(
                product_id, body=self._body(payload), actor=self._actor(actor_id)
            )
        except BackendAdminResponseUnusable:
            # Бэк ответил 2xx — правка СОСТОЯЛАСЬ, негодно только тело. Фиксируем факт
            # и пробрасываем ошибку дальше: оператор увидит 502, но след не потерян.
            on_applied(None)
            raise
        on_applied(self._tolerant(BackendProductUpdateResponse, raw))
        return self._validate(BackendProductUpdateResponse, raw)

    async def update_pricing(
        self,
        backend_id: uuid.UUID,
        tariff_id: str,
        payload: UpdateBackendTariffRequest,
        *,
        actor_id: uuid.UUID,
        on_applied: Callable[[BackendTariffUpdateResponse | None], None],
    ) -> BackendTariffUpdateResponse:
        """Правка тарифа списания — НЕ «отчётная цифра»: тот же тариф обслуживает у бэка
        пользовательские пути расчёта стоимости генерации.

        `on_applied` — то же, что у правки продукта (ADR-073 §8.3): аудит до разбора.
        """
        _, client = await self._sources.require(backend_id)
        try:
            raw = await client.update_pricing(
                tariff_id, body=self._body(payload), actor=self._actor(actor_id)
            )
        except BackendAdminResponseUnusable:  # 2xx с негодным телом — см. update_product
            on_applied(None)
            raise
        on_applied(self._tolerant(BackendTariffUpdateResponse, raw))
        return self._validate(BackendTariffUpdateResponse, raw)

    # --- внутреннее ---

    async def _fetch_capabilities(
        self, backend: Backend, client: BackendAdminClient
    ) -> BackendEconomicsCapabilities | None:
        """Необязательный подзапрос фич: неуспех ⇒ `None` + событие с машинной причиной.

        Fail-closed: без подтверждённых фич модуль read-only (карандаши не рендерятся
        ни при каком праве). Причина обязана попадать в лог — иначе «пропали карандаши»
        неотличимо от «бэк не поддерживает запись» (ADR-072 §7.1).
        """
        result = await client.get_capabilities()
        if result.data is None:
            self._log_capabilities_unavailable(backend, result.reason)
            return None
        try:
            return BackendEconomicsCapabilities.model_validate(result.data)
        except (ValidationError, TypeError):
            self._log_capabilities_unavailable(backend, REASON_SCHEMA_MISMATCH)
            return None

    @staticmethod
    def _log_capabilities_unavailable(backend: Backend, reason: str | None) -> None:
        # Имя события и перечень `reason` нормативны (ADR-072 §7.1) — ассертируются qa.
        # Значение `X-Admin-Key` в событие не попадает.
        logger.warning(
            "backend_admin_capabilities_unavailable",
            backend_id=str(backend.id),
            reason=reason or REASON_SCHEMA_MISMATCH,
        )

    @staticmethod
    def _parse_items(raw: dict[str, Any], schema: type[_ModelT]) -> list[_ModelT]:
        """Разбор `{"items": [...]}` со схемой элемента и лимитом не-пагинируемого списка."""
        raw_items = raw.get("items")
        if not isinstance(raw_items, list):
            raise backend_admin_unavailable(_CONTRACT_MISMATCH)
        if len(raw_items) > _MAX_ITEMS:
            raise backend_admin_unavailable(_CONTRACT_MISMATCH)
        try:
            return [schema.model_validate(item) for item in raw_items]
        except (ValidationError, TypeError) as exc:
            raise backend_admin_unavailable(_CONTRACT_MISMATCH) from exc

    @staticmethod
    def _body(payload: BaseModel) -> dict[str, Any]:
        """Тело PATCH для бэка: незаданные поля не отправляются, даты — ISO-строки.

        `updated_at: null` («ни разу не менялось») ⇒ `if_updated_at` не уходит вовсе.
        """
        return payload.model_dump(mode="json", exclude_none=True)

    @staticmethod
    def _actor(actor_id: uuid.UUID) -> str:
        return f"{_ACTOR_PREFIX}{actor_id}"

    @staticmethod
    def _tolerant(schema: type[_ModelT], raw: dict[str, Any]) -> _ModelT | None:
        """Разбор, который НЕ бросает: `None`, если ответ не соответствует схеме.

        Нужен там, где side-effect уже необратимо состоялся и решение (писать аудит)
        не вправе зависеть от качества ответа. Строгий разбор идёт следом и по-прежнему
        даёт `502` на не-контрактных данных — но уже ПОСЛЕ записи следа.
        """
        try:
            return schema.model_validate(raw)
        except (ValidationError, TypeError):
            return None

    @staticmethod
    def _validate(schema: type[_ModelT], raw: dict[str, Any]) -> _ModelT:
        try:
            return schema.model_validate(raw)
        except (ValidationError, TypeError) as exc:
            raise backend_admin_unavailable(_CONTRACT_MISMATCH) from exc


__all__ = ["BackendEconomicsService"]
