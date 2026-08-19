"""Бизнес-логика страницы «Пользователи бэков» (modules/backend-users, ADR-069/080).

Admin-ключ бэка расшифровывается в памяти обработчика и уходит только заголовком
`X-Admin-Key` (во frontend не попадает).

**Список читается из Postgres-снимка** (ADR-080 §2/§3 — норма ADR-069 §3 «прокси без
хранилища» для этого модуля отменена): один SQL с `JOIN backends` вместо десятков
upstream-запросов на рендер, сортировка `registered_at DESC, backend_id, user_id`,
окно merge ≤ 1000 упразднено. Снимок наполняет фоновый воркер
`BackendUsersSnapshotService`; его возраст виден оператору через `snapshot_at`.

**Точечные и пишущие пути остаются live** (§4): карточка, оплаты, запросы, тарифы,
начисление токенов, выдача подписки. После успешных admin-мутаций строка снимка
обновляется **best-effort** значениями из ответа бэка — провал touch'а не превращает
состоявшуюся операцию в ошибку.

**Бэки БЕЗ admin-ключа скрыты** (§1): они не опрашиваются и в `errors[]` не попадают
никогда — элемент `errors[]` означает «источник опрашивался и не ответил». Прод-инцидент
`selquro` закрыт другим средством: фильтр приложений на странице строится по
`has_admin_api_key`, а пустое состояние прямо говорит «подключите бэк с Admin API Key».
Режим ОДНОГО бэка не меняется: явный `backend_id` без ключа → `409
backend_admin_key_not_set` (осознанное действие оператора, а не фоновая конфигурация).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.errors import (
    AppError,
    backend_admin_unavailable,
)
from app.infra.backend_admin_client import BackendAdminClient
from app.logging import get_logger
from app.models.service_backend import Backend
from app.repositories.backend_repository import BackendRepository
from app.repositories.backend_user_snapshot_repository import (
    BackendUserSnapshotRepository,
    SnapshotRow,
    SnapshotSourceState,
)
from app.schemas.backend_user import (
    AddBackendUserTokensRequest,
    BackendProductsResponse,
    BackendUserDetailResponse,
    BackendUserGrantResponse,
    BackendUserItem,
    BackendUserPaymentsResponse,
    BackendUserRequestsResponse,
    BackendUsersApiCosts,
    BackendUsersListResponse,
    BackendUsersSourceError,
    BackendUsersStats,
    BackendUserTokensResponse,
    GrantBackendUserSubscriptionRequest,
)
from app.services.backend_admin_source import BackendAdminSourceResolver, BackendSource
from app.services.backend_users_snapshot_service import (
    PROVIDER_KEYS,
    PROVIDER_OTHER,
    normalize_provider,
)

logger = get_logger(__name__)

# Максимум одновременных admin-запросов при live fan-out `stats` (паттерн монитора).
_FANOUT_CONCURRENCY = 5

_CONTRACT_MISMATCH = "Бэк вернул данные не по контракту"


def _backend_fields(backend: Backend) -> dict[str, Any]:
    return {
        "backend_id": backend.id,
        "backend_code": backend.code,
        "backend_name": backend.name,
    }


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _parse_date_bound(raw: str | None, *, end_of_day: bool) -> datetime | None:
    """ISO-дата/датавремя фильтра периода → tz-aware граница; мусор → `None`.

    Голая дата (`2026-08-19`) как ВЕРХНЯЯ граница разворачивается в конец суток —
    иначе `date_to` отсекал бы всех, кто зарегистрировался в этот день позже полуночи.
    Неразбираемое значение не 400-ит запрос (сигнатура эндпоинта не менялась, а прежний
    путь просто транслировал строку бэку) — фильтр по этой границе не применяется.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if len(raw.strip()) == 10 and end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _snapshot_item(row: SnapshotRow) -> BackendUserItem:
    """Строка снимка → элемент контракта (форма ответа не меняется, ADR-080 §6)."""
    return BackendUserItem(
        backend_id=row.backend_id,
        backend_code=row.backend_code,
        backend_name=row.backend_name,
        id=row.user_id,
        external_id=row.external_id,
        is_paid=bool(row.is_paid),
        payments_count=row.payments_count or 0,
        renewals_count=row.renewals_count or 0,
        tokens=row.tokens or 0,
        subscription_active=bool(row.subscription_active),
        subscription_expires_at=row.subscription_expires_at,
        plan_id=row.plan_id,
        registered_at=row.registered_at,
    )


def _snapshot_at(
    backend_ids: list[uuid.UUID], states: list[SnapshotSourceState]
) -> datetime | None:
    """`MIN(refreshed_at)` по участвующим источникам; хоть один не собран → `None`.

    «Не собран» — это и источник без строки состояния (воркер до него не доходил), и
    строка с `refreshed_at IS NULL`: метка свежести обязана быть честной для ВСЕЙ
    выдачи, а не для её собранной части.
    """
    by_id = {state.backend_id: state for state in states}
    marks: list[datetime] = []
    for backend_id in backend_ids:
        state = by_id.get(backend_id)
        if state is None or state.refreshed_at is None:
            return None
        marks.append(state.refreshed_at)
    return min(marks) if marks else None


def _api_costs(
    backend_ids: list[uuid.UUID], states: list[SnapshotSourceState]
) -> BackendUsersApiCosts | None:
    """Агрегат «Расходы API» + `partial` (ADR-080 §5, нормативно).

    ```
    partial = ∃ участвующий источник:  revenue_backfill_done = false
                                       OR revenue_supported IS FALSE
    ```

    Второй дизъюнкт закрывает дыру: карточка бэка **уровня v1** (без блока `revenue`)
    при доборе тоже получает `revenue_refreshed_at` и покидает очередь ⇒
    `revenue_backfill_done` у него честно становится `true`, а сумма занижена НАВСЕГДА.
    Строго `IS FALSE`, а не `IS NOT TRUE`: состояние `NULL` («карточек ещё не добирали»)
    уже покрыто первым дизъюнктом, и дублировать его значило бы держать одно состояние
    в двух местах предиката.

    Признак читается со строк ИСТОЧНИКОВ — `O(число бэков)`; сканировать
    `backend_user_snapshots` (`api_cost_usd IS NULL`) ради того же вывода запрещено.

    Ни одного собранного источника → `None` («снимок ещё не сформирован»).
    """
    by_id = {state.backend_id: state for state in states}
    participating = [by_id[bid] for bid in backend_ids if bid in by_id]
    if not participating:
        return None

    totals = dict.fromkeys(PROVIDER_KEYS, 0.0)
    for state in participating:
        for provider, amount in state.api_costs.items():
            totals[normalize_provider(provider)] += float(amount or 0)

    partial = len(participating) < len(backend_ids) or any(
        not state.revenue_backfill_done or state.revenue_supported is False
        for state in participating
    )
    return BackendUsersApiCosts(
        openai_usd=round(totals["openai"], 6),
        anthropic_usd=round(totals["anthropic"], 6),
        fal_usd=round(totals["fal"], 6),
        other_usd=round(totals[PROVIDER_OTHER], 6),
        total_usd=round(sum(totals.values()), 6),
        partial=partial,
    )


class BackendUserService:
    """Список из снимка + live-транзит CRM Admin API для страницы «Пользователи бэков»."""

    def __init__(
        self,
        repository: BackendRepository,
        snapshots: BackendUserSnapshotRepository | None = None,
    ) -> None:
        self._repo = repository
        # Расшифровка admin-ключа — общий security-critical путь двух модулей
        # (services/backend_admin_source.py, ADR-072 §Последствия).
        self._sources = BackendAdminSourceResolver(repository)
        # Снимок (ADR-080): read-path списка и best-effort touch после admin-мутаций.
        self._snapshots_override = snapshots

    @property
    def _snapshots(self) -> BackendUserSnapshotRepository:
        """Репозиторий снимка — **ленивый** (создаётся при первом обращении).

        Ленивость не косметическая: чисто live-пути (карточка, оплаты, тарифы) снимка не
        касаются вовсе, и требовать сессию БД на конструирование сервиса ради них значило
        бы связать транзит с хранилищем, которого он не использует.
        """
        if self._snapshots_override is None:
            self._snapshots_override = BackendUserSnapshotRepository(self._repo.session)
        return self._snapshots_override

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
        """Список пользователей + сводка ИЗ СНИМКА (04-api.md#get-apibackend-users).

        `backend_id=None` — режим «Все приложения» (участвуют бэки с admin-ключом);
        явный `backend_id` — тот же SQL, но `404`/`409` резолвера сохраняются.

        - `items`/`total` — один SQL по снимку с `JOIN backends`, стабильный порядок
          `registered_at DESC, backend_id, user_id`, `LIMIT/OFFSET` + `COUNT`. Глубина
          пагинации ничем не ограничена (окно merge ≤ 1000 упразднено, ADR-080 §3).
        - `stats` **без периода** — суммы `stats_*` строк источников; `cr_percent`
          считает CRM. **С периодом — live fan-out ТОЛЬКО `GET {P}/stats`** (один запрос
          на бэк): периодные суммы из снимка невыводимы — он хранит текущее состояние
          пользователя, а не историю платежей.
        - `errors[]` — источники с `error_message IS NOT NULL` (+ сбои live-`stats`).
        - `snapshot_at` — `MIN(refreshed_at)`; хотя бы один источник ни разу не
          обновлялся → `null` (UI: «Снимок формируется…»).
        - `api_costs` — lifetime-агрегат расходов + `partial` (§5).
        """
        sources = await self._resolve_sources(backend_id)
        if not sources:
            return BackendUsersListResponse(total=0, items=[], stats=BackendUsersStats())

        backend_ids = [backend.id for backend, _client in sources]
        states = await self._snapshots.source_states(backend_ids)

        rows, total = await self._snapshots.list_page(
            backend_ids=backend_ids,
            search=search,
            date_from=_parse_date_bound(date_from, end_of_day=False),
            date_to=_parse_date_bound(date_to, end_of_day=True),
            is_paid=is_paid,
            limit=limit,
            offset=offset,
        )

        errors = self._snapshot_errors(sources, states)
        has_period = bool(date_from or date_to)
        if has_period:
            stats = await self._live_period_stats(sources, date_from, date_to, errors)
        else:
            stats = self._stats_from_states(states)
        if stats.users_total > 0:
            stats.cr_percent = round(stats.paid_users / stats.users_total * 100, 1)

        return BackendUsersListResponse(
            total=total,
            items=[_snapshot_item(row) for row in rows],
            stats=stats,
            errors=errors,
            snapshot_at=_snapshot_at(backend_ids, states),
            api_costs=_api_costs(backend_ids, states),
        )

    @staticmethod
    def _stats_from_states(states: list[SnapshotSourceState]) -> BackendUsersStats:
        """Сводка без периода — суммы `stats_*` строк источников (ни одного запроса вовне)."""
        stats = BackendUsersStats()
        for state in states:
            stats.users_total += state.stats_users_total
            stats.paid_users += state.stats_paid_users
            stats.payments_sum_usd += state.stats_payments_sum_usd
        return stats

    async def _live_period_stats(
        self,
        sources: list[BackendSource],
        date_from: str | None,
        date_to: str | None,
        errors: list[BackendUsersSourceError],
    ) -> BackendUsersStats:
        """Сводка с периодом — **один** live-`GET {P}/stats` на источник (ADR-080 §3).

        Дорогой путь явно ограничен одним запросом на бэк: периодные суммы из снимка
        невыводимы. Сбой источника не роняет ответ — он добавляется в `errors[]`.
        """
        semaphore = asyncio.Semaphore(_FANOUT_CONCURRENCY)

        async def fetch(client: BackendAdminClient) -> dict[str, Any]:
            async with semaphore:
                return await client.get_stats(date_from=date_from, date_to=date_to)

        results = await asyncio.gather(
            *(fetch(client) for _backend, client in sources), return_exceptions=True
        )
        stats = BackendUsersStats()
        for (backend, _client), result in zip(sources, results, strict=True):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                errors.append(self._source_error(backend, result))
                continue
            self._accumulate_stats(stats, backend, result, errors)
        return stats

    @staticmethod
    def _snapshot_errors(
        sources: list[BackendSource], states: list[SnapshotSourceState]
    ) -> list[BackendUsersSourceError]:
        """`errors[]` = источники со сбоем последнего цикла воркера (ADR-080 §1/§3).

        Бэка без admin-ключа здесь нет никогда — он вообще не попадает в `sources`.
        Источник без строки состояния (воркер ещё не доходил) ошибкой НЕ считается: это
        «снимок формируется», и об этом говорит `snapshot_at: null`.
        """
        failures = {
            state.backend_id: state.error_message
            for state in states
            if state.error_message is not None
        }
        return [
            BackendUsersSourceError(**_backend_fields(backend), message=failures[backend.id])
            for backend, _client in sources
            if backend.id in failures
        ]

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
                    **_backend_fields(backend),
                    message=_CONTRACT_MISMATCH,
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
        return BackendUsersSourceError(**_backend_fields(backend), message=message)

    # --- карточка / истории / тарифы ---

    async def get_user(self, backend_id: uuid.UUID, user_id: str) -> BackendUserDetailResponse:
        backend, client = await self._require_source(backend_id)
        raw = await client.get_user(user_id)
        try:
            return BackendUserDetailResponse.model_validate({**raw, **_backend_fields(backend)})
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
        response = self._validate(BackendUserTokensResponse, raw)
        await self._touch_snapshot(backend_id, user_id, tokens=response.tokens)
        return response

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
        response = self._validate(BackendUserGrantResponse, raw)
        await self._touch_snapshot(
            backend_id,
            user_id,
            tokens=response.tokens,
            subscription_active=response.subscription_active,
            subscription_expires_at=response.subscription_expires_at,
            touch_subscription=True,
        )
        return response

    async def _touch_snapshot(
        self,
        backend_id: uuid.UUID,
        user_id: str,
        *,
        tokens: float | None = None,
        subscription_active: bool | None = None,
        subscription_expires_at: datetime | None = None,
        touch_subscription: bool = False,
    ) -> None:
        """Best-effort обновление строки снимка после успешной admin-операции (ADR-080 §4).

        Без него оператор, начисливший токены, видел бы в списке старое значение до
        следующего цикла воркера — самый заметный случай расхождения свежести.

        **Пишутся только ИЗМЕРЕННЫЕ величины** (ADR-072 §5): `tokens=None` («бэк поля не
        отдал») колонку НЕ трогает — иначе выдача плана, которая баланс не меняет,
        затирала бы реальный баланс нулём.

        **Провал touch'а НЕ превращает состоявшуюся операцию в ошибку** (тот же принцип
        «сначала факт, затем интерпретация», что в ADR-073 §8): у бэка изменение уже
        применено, и 500 из-за не обновившегося зеркала подтолкнул бы оператора повторить
        НЕидемпотентное начисление токенов.
        """
        try:
            await self._snapshots.touch_row(
                backend_id=backend_id,
                user_id=user_id,
                tokens=tokens,
                subscription_active=subscription_active,
                subscription_expires_at=subscription_expires_at,
                touch_subscription=touch_subscription,
            )
            await self._snapshots.session.commit()
        except Exception as exc:
            # `rollback()` обязателен: сессия запроса общая, и после сбоя statement'а
            # Postgres держит транзакцию в состоянии «aborted» — без отката ЛЮБОЙ
            # следующий SQL в этом же запросе упал бы `InFailedSQLTransactionError`,
            # то есть провал best-effort touch'а всё-таки уронил бы ответ.
            with suppress(Exception):
                await self._snapshots.session.rollback()
            logger.warning(
                "backend_users_snapshot_touch_failed",
                backend_id=str(backend_id),
                error_type=type(exc).__name__,
            )

    # --- источники ---

    async def _resolve_sources(self, backend_id: uuid.UUID | None) -> list[BackendSource]:
        """Участвующие источники: бэки с admin-ключом (ADR-080 §1).

        Бэк без ключа в режиме «Все приложения» просто не участвует — ни в выборке, ни в
        `errors[]`. Для ОДНОГО бэка отсутствие ключа остаётся явной ошибкой
        `409 backend_admin_key_not_set` (осознанное действие оператора).
        """
        if backend_id is not None:
            return [await self._require_source(backend_id)]
        return await self._sources.list_with_admin_key()

    async def _require_source(self, backend_id: uuid.UUID) -> BackendSource:
        return await self._sources.require(backend_id)

    @staticmethod
    def _validate(schema: type[_ModelT], raw: dict[str, Any]) -> _ModelT:
        try:
            return schema.model_validate(raw)
        except (ValidationError, TypeError) as exc:
            raise backend_admin_unavailable(_CONTRACT_MISMATCH) from exc
