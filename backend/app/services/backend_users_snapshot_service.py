"""Фоновый воркер Postgres-снимка «Юзеров бэков» (modules/backend-users, ADR-080 §2/§5).

Отдельная asyncio-задача по канону `BackendMonitorService`: немедленный `refresh_once()`
при старте, затем `while True: refresh_once(); await asyncio.sleep(interval)`. `sleep`
идёт **после** завершения итерации (а не фиксированным тиком) ⇒ циклы не накладываются,
даже если полный обход крупного бэка занял больше интервала. Исключение итерации
логируется и задачу не валит. Брокер не вводится (ADR-006/NFR-1) — Postgres уже есть, а
снимок переживает рестарт.

Алгоритм на один бэк (fan-out под семафором `BACKEND_USERS_SNAPSHOT_CONCURRENCY`, по
умолчанию 2 — не 5, как у интерактивного fan-out страницы: воркер шлёт тысячи запросов
подряд, а бэки нередко живут на ОДНОМ сервере, и параллельный обход дожимал их до
`429`/`500`, Q-BU-2). Все upstream-вызовы цикла идут через `_with_retry` (backoff на
`429`/`5xx`) и разделяются паузой `_throttle`:

1. fingerprints снимка одним `SELECT`;
2. **полный** постраничный обход `GET {P}/users?limit=100` **без** окна `_MAX_WINDOW`
   (оно защищало интерактивный запрос; здесь запрос фоновый) — пишутся только новые и
   изменившиеся строки (батч-upsert), их множество = **dirty-set**;
3. `DELETE` исчезнувших из источника — **только при полностью успешном обходе**;
4. `GET {P}/stats` → `stats_*` строки источника;
5. экономика по dirty-set + backfill-квота; по **первой успешно добранной карточке
   цикла** выставляется `revenue_supported`;
6. пересчёт `api_costs` источника: `SUM` по сырым ключам + нормализация провайдеров;
   `refreshed_at = now()`, `error_message`/`failed_at` обнуляются;
7. **любое исключение** → прошлый снимок НЕ трогается, в строку источника пишутся
   `error_message`/`failed_at`. Устаревшие данные с честной меткой возраста лучше
   пустого экрана.

**Сессия БД НЕ удерживается на время HTTP** (канон `BackendMonitorService.poll_once`:
снимок в память → сессия закрыта → далее только сеть и короткие `UPDATE`). Каждое
обращение к БД здесь — отдельная короткая сессия (`_in_session`), а обход и добор
карточек идут вообще без открытой сессии. Иначе полный обход крупного бэка держал бы
транзакцию в `idle in transaction` минутами, а семафор fan-out при пуле того же порядка
выбирал бы весь пул — и интерактивные запросы API уходили бы в `pool_timeout`.
"""

from __future__ import annotations

import asyncio
import functools
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.errors import (
    AppError,
    BackendAdminResponseUnusable,
    BackendAdminUpstreamStatus,
    backend_admin_unavailable,
)
from app.logging import get_logger
from app.repositories.backend_repository import BackendRepository
from app.repositories.backend_user_snapshot_repository import (
    BackendUserSnapshotRepository,
    Fingerprint,
    build_fingerprint,
)
from app.schemas.backend_user import BackendUserDetailResponse, BackendUserItem, BackendUsersStats
from app.services.backend_admin_source import BackendAdminSourceResolver

logger = get_logger(__name__)

# Статусы источника, которые считаются ВРЕМЕННЫМ отказом и лечатся повтором (Q-BU-2).
# `429` — rate-limit, `5xx` — сбой на стороне бэка. Прочие исходы (`3xx`, `401/403`,
# `404`, `400/422`, `409`, таймаут/транспорт) постоянны в пределах цикла: повтор их не
# лечит, а лишь добавляет нагрузки источнику, который уже отвечает.
_RETRYABLE_STATUS = 429
_SERVER_ERROR_MIN = 500

# Страница источника по контракту (§2.1: limit <= 100).
_SOURCE_PAGE_LIMIT = 100

# Жёсткий потолок числа страниц одного обхода — защита от «бесконечной» пагинации, если
# бэк отдаёт непустые страницы вечно (сбитый offset/total). При 100 строк на страницу это
# 5 млн пользователей: штатный обход в него не упирается, а зацикливание — упирается.
_MAX_PAGES = 50_000

# Нормализация провайдера (ADR-080 §5, нормативно). Сопоставление регистронезависимое.
#
# **Точные алиасы, а не «любой префикс».** ADR помечает звёздочкой только семейства
# моделей (`gpt*`, `claude*`) — их бэк называет как `gpt-4o`/`claude-3-opus`, поэтому там
# префикс обязателен. Для самих провайдеров звёздочки нет, и брать префикс было бы
# ошибкой: `falcon` начинается на `fal` и молча попал бы в расходы Fal, а `openai-proxy`
# постороннего вендора — в расходы OpenAI. Незнакомое имя обязано уходить в `other`, где
# оно видно как «прочее», а не подмешиваться в чужую строку сводки.
_PROVIDER_ALIASES: dict[str, str] = {
    "openai": "openai",
    "open-ai": "openai",
    "open_ai": "openai",
    "anthropic": "anthropic",
    "fal": "fal",
    "fal.ai": "fal",
    "fal_ai": "fal",
    "fal-ai": "fal",
}
# Префиксы ТОЛЬКО для семейств моделей (звёздочка в ADR-080 §5).
_PROVIDER_MODEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("gpt", "openai"),
    ("claude", "anthropic"),
)
# Незнакомый провайдер НЕ теряется — он попадает сюда, а не отбрасывается.
PROVIDER_OTHER = "other"

# Ключи агрегата `api_costs` (они же поля `BackendUsersApiCosts`).
PROVIDER_KEYS: tuple[str, ...] = ("openai", "anthropic", "fal", PROVIDER_OTHER)


def normalize_provider(raw: str) -> str:
    """Канонический провайдер расходов (ADR-080 §5, нормативно).

    `openai`/`gpt*` → `openai`; `anthropic`/`claude*` → `anthropic`;
    `fal`/`fal.ai`/`fal_ai` → `fal`; всё остальное → `other`.

    Имя провайдера сопоставляется ПО ЗНАЧЕНИЮ (таблица алиасов); по префиксу — только
    семейства моделей `gpt*`/`claude*`. См. комментарий к `_PROVIDER_ALIASES`.
    """
    value = raw.strip().lower()
    alias = _PROVIDER_ALIASES.get(value)
    if alias is not None:
        return alias
    for prefix, canon in _PROVIDER_MODEL_PREFIXES:
        if value.startswith(prefix):
            return canon
    return PROVIDER_OTHER


@dataclass
class _BackendResult:
    """Итог обхода одного бэка (для лога; наружу не отдаётся)."""

    changed: int = 0
    deleted: int = 0
    revenue_fetched: int = 0
    # Все `user_id`, встреченные в источнике за обход — база разности для `delete_rows`.
    tracked_users: set[str] = field(default_factory=set)
    # Изменившиеся (dirty-set) — вход инкрементального добора экономики (§5).
    dirty_users: set[str] = field(default_factory=set)
    # Дошёл ли обход до КОНЦА источника. `False` — аварийный обрыв (сбитая пагинация,
    # упор в `_MAX_PAGES`): `tracked_users` тогда НЕ является полным списком живых
    # пользователей, и разность с ним — не «исчезнувшие», а «недосмотренные».
    # Инвариант ADR-080 §2 п.3 (`DELETE` только при ПОЛНОСТЬЮ успешном обходе) держится
    # на этом признаке: без него оборванный обход прореживал бы снимок, а цикл при этом
    # записывался бы успешным — потеря данных без единого сигнала оператору.
    complete: bool = True
    # Причина обрыва — уходит в `error_message` строки источника (оператору в `errors[]`).
    incomplete_reason: str = ""


class BackendUsersSnapshotService:
    """Периодический сбор снимка пользователей бэков + агрегата расходов API."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._interval_sec = settings.backend_users_snapshot_interval_sec
        self._revenue_batch = settings.backend_users_snapshot_revenue_batch
        self._concurrency: int = settings.backend_users_snapshot_concurrency
        self._page_delay_sec: float = settings.backend_users_snapshot_page_delay_sec
        self._retry_attempts: int = settings.backend_users_snapshot_retry_attempts
        self._retry_base_sec: float = settings.backend_users_snapshot_retry_base_sec
        self._retry_cap_sec: float = settings.backend_users_snapshot_retry_cap_sec

    async def run(self) -> None:
        """Бесконечный цикл: обход → sleep. Ошибка итерации логируется, цикл живёт."""
        logger.info("backend_users_snapshot_started", interval=self._interval_sec)
        try:
            while True:
                try:
                    await self.refresh_once()
                except Exception as exc:  # итерация не должна валить фоновую задачу
                    logger.error("backend_users_snapshot_failed", error_type=type(exc).__name__)
                await asyncio.sleep(self._interval_sec)
        except asyncio.CancelledError:
            logger.info("backend_users_snapshot_stopped")
            raise

    async def refresh_once(self) -> None:
        """Одна итерация: fan-out по бэкам с admin-ключом под семафором.

        Бэки БЕЗ admin-ключа не опрашиваются и строк источника не заводят (ADR-080 §1:
        `errors[]` означает только реальный сбой).
        """
        async with self._sessionmaker() as session:
            resolver = BackendAdminSourceResolver(BackendRepository(session))
            sources = await resolver.list_with_admin_key()
            targets = [(backend.id, backend.code, client) for backend, client in sources]

        if not targets:
            return

        semaphore = asyncio.Semaphore(self._concurrency)

        async def _guarded(backend_id: uuid.UUID, code: str, client: Any) -> None:
            async with semaphore:
                await self._refresh_backend(backend_id, code, client)

        await asyncio.gather(*(_guarded(bid, code, client) for bid, code, client in targets))

    async def _refresh_backend(self, backend_id: uuid.UUID, code: str, client: Any) -> None:
        """Полный цикл одного бэка. Исключение → снимок не трогается, сбой в строке источника."""
        try:
            await self._refresh_backend_inner(backend_id, client)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = getattr(exc, "message", None) or "Бэк не ответил на admin-запрос"
            logger.warning(
                "backend_users_snapshot_source_failed",
                backend_id=str(backend_id),
                code=code,
                error_type=type(exc).__name__,
            )
            await self._record_failure(backend_id, str(message))

    # --- Бережность к источнику (Q-BU-2) ---------------------------------------
    #
    # Обе паузы ниже вызываются ТОЛЬКО там, где сессия БД закрыта (обход и добор карточек
    # идут вообще без открытой сессии, см. docstring модуля). Пауза с открытой сессией
    # держала бы транзакцию `idle in transaction` на все секунды backoff'а и выбирала бы
    # пул — ровно то, от чего уводит канон коротких сессий.

    async def _throttle(self) -> None:
        """Межстраничная пауза обхода/добора. `0` — троттлинг выключен."""
        if self._page_delay_sec > 0:
            await asyncio.sleep(self._page_delay_sec)

    async def _with_retry(
        self,
        backend_id: uuid.UUID,
        operation: str,
        call: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Повтор upstream-вызова с exponential backoff на `429`/`5xx` (Q-BU-2, вариант «б»).

        Ретраи существуют ТОЛЬКО в воркере: он один ходит в бэк тысячами запросов подряд
        и сам же доводит источник до rate-limit. Интерактивные пути API (карточка,
        мутации) остаются без повторов — там ждёт человек, и лишние 30 секунд хуже
        честной ошибки.

        Исчерпание попыток НЕ меняет прежнего контракта отказа: наружу уходит та же
        ошибка ⇒ цикл бэка падает, `error_message`/`failed_at` пишутся, снимок прошлого
        цикла цел (§2 п.7).
        """
        attempt = 0
        while True:
            try:
                return await call()
            except BackendAdminUpstreamStatus as exc:
                retryable = (
                    exc.upstream_status == _RETRYABLE_STATUS
                    or exc.upstream_status >= _SERVER_ERROR_MIN
                )
                if not retryable or attempt >= self._retry_attempts - 1:
                    raise
                delay = self._retry_delay(attempt, exc.retry_after_sec)
                logger.warning(
                    "backend_users_snapshot_upstream_retry",
                    backend_id=str(backend_id),
                    operation=operation,
                    upstream_status=exc.upstream_status,
                    attempt=attempt + 1,
                    delay_sec=round(delay, 3),
                )
                await asyncio.sleep(delay)
                attempt += 1

    def _retry_delay(self, attempt: int, retry_after_sec: float | None) -> float:
        """Задержка перед повтором: `Retry-After` источника, иначе backoff с джиттером.

        `Retry-After` уважается как есть (но не дольше потолка): бэк лучше нас знает,
        когда откроется его окно. Без заголовка — `base * 2^attempt` с потолком `cap` и
        джиттером: без джиттера все бэки-соседи по серверу, упавшие в rate-limit
        одновременно, вернулись бы к нему тоже одновременно.
        """
        if retry_after_sec is not None:
            return min(retry_after_sec, self._retry_cap_sec)
        window = min(self._retry_base_sec * (2.0**attempt), self._retry_cap_sec)
        # `random` здесь не криптографический примитив, а разброс момента повтора.
        return window / 2 + random.uniform(0, window / 2)

    @asynccontextmanager
    async def _in_session(self) -> AsyncIterator[BackendUserSnapshotRepository]:
        """Короткая сессия под ОДНО обращение к БД: открыть → записать → commit → закрыть.

        Каждый шаг цикла берёт соединение ровно на время своего SQL. Держать сессию
        открытой через HTTP-вызовы запрещено: обход крупного бэка занимает минуты, и
        транзакция всё это время висела бы `idle in transaction`, а семафор fan-out
        параллельных бэков выбрал бы пул целиком — интерактивные запросы API уходили бы
        в `pool_timeout`.
        """
        async with self._sessionmaker() as session:
            repo = BackendUserSnapshotRepository(session)
            yield repo
            await session.commit()

    async def _refresh_backend_inner(self, backend_id: uuid.UUID, client: Any) -> None:
        # 1. Fingerprints — короткая сессия, дальше работаем по памяти.
        async with self._in_session() as repo:
            known = await repo.fingerprints(backend_id)

        # 2. Полный обход источника. Сессия ЗАКРЫТА: только HTTP + короткие сессии на
        #    батч-upsert изменившихся строк.
        result = await self._walk_users(backend_id, client, known)

        # 2а. Обход оборвался, не дойдя до конца источника (сбитая пагинация, упор в
        #     `_MAX_PAGES`) — это ОТКАЗ цикла, а не его успешное завершение. Ошибка
        #     поднимается ДО шага 3: иначе `tracked_users` (заведомо неполный) ушёл бы в
        #     разность, и `DELETE` вычистил бы из снимка всех, до кого обход не добрался,
        #     а строка источника получила бы `refreshed_at` без единого признака беды.
        #     Прежний снимок остаётся целым, причина видна оператору в `errors[]` (§2 п.7).
        if not result.complete:
            raise backend_admin_unavailable(result.incomplete_reason)

        # 3. `DELETE` — строго после ПОЛНОГО успешного обхода (исключение выше сюда не
        #    доходит): оборванный обход снимок не прореживает. Разность считается по
        #    памяти, в SQL уходят только реально исчезнувшие id.
        missing = known.keys() - result.tracked_users
        if missing:
            async with self._in_session() as repo:
                result.deleted = await repo.delete_rows(backend_id, missing)

        # 4. Сводка источника — HTTP без открытой сессии.
        stats = self._parse_stats(await self._with_retry(backend_id, "get_stats", client.get_stats))

        # 5. Экономика: HTTP-добор карточек, запись — короткими сессиями внутри.
        supported, fetched, revenue_complete = await self._refresh_revenue(
            backend_id, client, dirty=result.dirty_users
        )
        result.revenue_fetched = fetched

        # 6. Пересчёт агрегата и запись состояния источника — одна короткая сессия.
        async with self._in_session() as repo:
            pending = await repo.count_pending_revenue(backend_id)
            api_costs = await self._aggregate_api_costs(repo, backend_id)

            values: dict[str, Any] = {
                "refreshed_at": datetime.now(UTC),
                "error_message": None,
                "failed_at": None,
                "stats_users_total": stats.users_total,
                "stats_paid_users": stats.paid_users,
                "stats_payments_sum_usd": stats.payments_sum_usd,
                "api_costs": api_costs,
                # Прервавшаяся фаза экономики НЕ считается завершённым backfill'ом,
                # даже если очередь опустела: иначе `partial` погас бы при заведомо
                # неполных расходах (ADR-080 §5 — первый дизъюнкт формулы).
                "revenue_backfill_done": pending == 0 and revenue_complete,
            }
            # `revenue_supported` пересматривается КАЖДЫЙ цикл (бэк, внедривший v1.1,
            # переключается в `true` сам), но только если карточку удалось добрать: иначе
            # прошлое значение сохраняется, а не затирается в NULL.
            if supported is not None:
                values["revenue_supported"] = supported
            await repo.upsert_source(backend_id, values)

        logger.info(
            "backend_users_snapshot_refreshed",
            backend_id=str(backend_id),
            changed=result.changed,
            deleted=result.deleted,
            revenue_fetched=result.revenue_fetched,
            total=len(result.tracked_users),
        )

    async def _walk_users(
        self,
        backend_id: uuid.UUID,
        client: Any,
        known: dict[str, Fingerprint],
    ) -> _BackendResult:
        """Полный постраничный обход `GET {P}/users` с changed-only батч-upsert'ом.

        Сессия БД **не удерживается**: HTTP-страница читается без соединения, а батч
        изменившихся строк пишется отдельной короткой сессией (`_in_session`).

        Между страницами выдерживается пауза (`_throttle`), а сама страница берётся с
        ретраями на `429`/`5xx`: обход крупного бэка — тысячи запросов подряд, и без
        обоих механизмов воркер сам доводит источник до rate-limit (Q-BU-2).
        """
        result = _BackendResult()
        offset = 0
        for page_no in range(_MAX_PAGES):
            if page_no:
                await self._throttle()
            raw = await self._with_retry(
                backend_id,
                "list_users",
                functools.partial(client.list_users, limit=_SOURCE_PAGE_LIMIT, offset=offset),
            )
            items = self._parse_users_page(raw)
            if not items:
                break

            # Защита от холостого обхода (Q-BU-2, вариант «а»): страница без единого
            # НОВОГО `user_id` означает сбитую пагинацию источника (повтор/зацикливание
            # окна). Продолжать бессмысленно — дальше те же строки, а цена ошибки —
            # десятки тысяч запросов к бэку до упора в `_MAX_PAGES`. Признак —
            # бесполезность страницы, а не счётчик.
            fresh = [item for item in items if item.id not in result.tracked_users]
            if not fresh:
                logger.warning(
                    "backend_users_snapshot_walk_stalled",
                    backend_id=str(backend_id),
                    offset=offset,
                    page_size=len(items),
                )
                # Обрыв АВАРИЙНЫЙ: до конца источника обход не дошёл, значит список
                # живых пользователей неполон и разностью пользоваться нельзя.
                result.complete = False
                result.incomplete_reason = "Источник повторяет страницу — обход прерван"
                break

            changed_rows: list[dict[str, Any]] = []
            for item in fresh:
                result.tracked_users.add(item.id)
                row = self._row_values(backend_id, item)
                if known.get(item.id) == build_fingerprint(row):
                    continue
                changed_rows.append(row)
                result.dirty_users.add(item.id)
            if changed_rows:
                async with self._in_session() as repo:
                    await repo.upsert_rows(changed_rows)
                result.changed += len(changed_rows)

            offset += len(items)
            if len(items) < _SOURCE_PAGE_LIMIT:
                break  # неполная страница = конец источника: обход ЗАВЕРШЁН штатно
        else:
            # Цикл исчерпал `_MAX_PAGES`, ни разу не встретив конца источника, — тот же
            # класс дефекта, что и повтор страницы: обход неполон, разностью пользоваться
            # нельзя. Без этой ветки упор в потолок молча давал бы «успешный» цикл с
            # `DELETE` всех непросмотренных строк.
            logger.warning(
                "backend_users_snapshot_walk_truncated",
                backend_id=str(backend_id),
                pages=_MAX_PAGES,
                tracked=len(result.tracked_users),
            )
            result.complete = False
            result.incomplete_reason = "Источник не отдал конец списка — обход прерван"
        return result

    async def _refresh_revenue(
        self,
        backend_id: uuid.UUID,
        client: Any,
        *,
        dirty: set[str],
    ) -> tuple[bool | None, int, bool]:
        """Экономика по dirty-set + backfill-квота. Возвращает `(supported, N, complete)`.

        Инкрементально: изменившийся fingerprint = пользователь что-то потратил ⇒ один
        `GET {P}/users/{id}`. Пассивные пользователи не опрашиваются вовсе.

        **Квота `BACKEND_USERS_SNAPSHOT_REVENUE_BATCH` — потолок на ВЕСЬ цикл**, а не
        только на хвост добора: dirty-set сперва усекается до квоты, и лишь остаток
        добирается из очереди `revenue_refreshed_at IS NULL` (порядок `registered_at
        DESC` — свежие пользователи ценнее). Иначе холодный старт, где «изменилось» ВСЁ
        (снимок пуст ⇒ каждая строка новая), дал бы 305 000 upstream-запросов за цикл
        вместо 2000 — ровно та стоимость, ради ухода от которой отвергнут краулинг
        `/requests` (ADR-080 §5). Усечённый хвост dirty-set не теряется: у этих строк
        `revenue_refreshed_at` остаётся `NULL`, и они сами стоят в очереди backfill.

        `revenue_supported` определяется по **первой успешно добранной карточке цикла**
        (блок `revenue` есть → `True`, нет → `False`); ни одной карточки — `None`
        («не пересматривать»). `sorted`, а не порядок множества: при нестабильном порядке
        признак «прыгал бы» между циклами на бэке со смешанными ответами.
        """
        candidates = sorted(dirty)[: self._revenue_batch]
        quota = self._revenue_batch - len(candidates)
        if quota > 0:
            async with self._in_session() as repo:
                backfill = await repo.backfill_candidates(backend_id, quota)
            candidates.extend(user_id for user_id in backfill if user_id not in dirty)

        supported: bool | None = None
        fetched = 0
        complete = True
        for index, user_id in enumerate(candidates):
            # Пауза между карточками — вне открытой сессии (запись идёт ниже, короткой).
            if index:
                await self._throttle()
            try:
                detail = await self._fetch_detail(backend_id, client, user_id)
            except AppError as exc:
                # ⚠️ Прод-инцидент 2026-08-20: несколько бэков стабильно отдают `500` на
                # карточку ОТДЕЛЬНЫХ пользователей (список, `/stats` и `/products` у них
                # при этом исправны — проверено сырым запросом). Пока отказ карточки
                # ронял цикл, эти бэки НИКОГДА не собирали снимок: строки списка уже были
                # верны, но `refreshed_at` не проставлялся, и вся страница показывала
                # «Снимок формируется…».
                #
                # Фаза экономики ВТОРИЧНА по отношению к списку: её отказ означает
                # «расходы неполны», а не «список недостоверен». Поэтому цикл
                # продолжается, а неполнота честно уходит в UI флагом `partial`
                # (`revenue_backfill_done` не выставляется). Это НЕ возврат к прежнему
                # «глотанию всего подряд»: сигнал о неполноте сохранён, отказ
                # залогирован, и обход списка по-прежнему роняет цикл.
                logger.warning(
                    "backend_users_snapshot_revenue_degraded",
                    backend_id=str(backend_id),
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    fetched=fetched,
                )
                complete = False
                break
            if detail is None:
                continue
            if supported is None:
                supported = detail.revenue is not None
            async with self._in_session() as repo:
                await repo.set_revenue(
                    backend_id=backend_id,
                    user_id=user_id,
                    api_cost_usd=detail.revenue.api_cost_usd if detail.revenue else None,
                    providers=dict(detail.revenue.providers) if detail.revenue else None,
                    refreshed_at=datetime.now(UTC),
                )
            fetched += 1
        return supported, fetched, complete

    async def _fetch_detail(
        self, backend_id: uuid.UUID, client: Any, user_id: str
    ) -> BackendUserDetailResponse | None:
        """Карточка пользователя или `None`, если ЭТОГО пользователя у бэка нет/ответ негоден.

        **Глотаются ровно два исхода** — оба означают «проблема с одной карточкой», а не с
        источником: `404 backend_user_not_found` (пользователь удалён у бэка между обходом
        списка и добором) и `backend_admin_response_unusable` (`2xx` с негодным телом,
        ADR-073 §8.3), плюс несоответствие схемы контракта.

        **Всё остальное пробрасывается** и роняет цикл этого бэка. Прежняя редакция ловила
        голый `Exception`: тайм-аут, `502`, отвергнутый admin-ключ молча превращались в
        «карточка пропущена», цикл записывался УСПЕШНЫМ (`refreshed_at`, `error_message =
        NULL`), а `api_costs` уходил в UI **занижённым и без единого признака беды** —
        `partial` при завершённом backfill даже не поднялся бы. Отказ источника обязан
        быть отказом цикла: снимок прошлого цикла сохраняется, а причина видна в `errors[]`.
        """
        try:
            raw = await self._with_retry(
                backend_id, "get_user", functools.partial(client.get_user, user_id)
            )
        except BackendAdminResponseUnusable as exc:
            logger.info(
                "backend_users_snapshot_detail_unusable",
                user_id=user_id,
                message=exc.message,
            )
            return None
        except AppError as exc:
            if exc.code != "backend_user_not_found":
                raise
            logger.info("backend_users_snapshot_detail_missing", user_id=user_id)
            return None
        try:
            return BackendUserDetailResponse.model_validate(
                {
                    **raw,
                    "backend_id": uuid.UUID(int=0),
                    "backend_code": "",
                    "backend_name": "",
                }
            )
        except (ValidationError, TypeError):
            logger.info("backend_users_snapshot_detail_unusable", user_id=user_id)
            return None

    async def _aggregate_api_costs(
        self, repo: BackendUserSnapshotRepository, backend_id: uuid.UUID
    ) -> dict[str, float]:
        """`SUM` по сырым ключам провайдеров → нормализованный агрегат `api_costs`."""
        raw_sums = await repo.sum_providers(backend_id)
        totals = dict.fromkeys(PROVIDER_KEYS, 0.0)
        for provider, amount in raw_sums.items():
            totals[normalize_provider(provider)] += float(amount or 0)
        return totals

    async def _record_failure(self, backend_id: uuid.UUID, message: str) -> None:
        """Пишет сбой цикла в строку источника. Прошлый снимок НЕ трогается (§2 п.7)."""
        try:
            async with self._in_session() as repo:
                await repo.upsert_source(
                    backend_id,
                    {"error_message": message[:500], "failed_at": datetime.now(UTC)},
                )
        except Exception as exc:
            logger.error(
                "backend_users_snapshot_failure_unrecorded",
                backend_id=str(backend_id),
                error_type=type(exc).__name__,
            )

    @staticmethod
    def _row_values(backend_id: uuid.UUID, item: BackendUserItem) -> dict[str, Any]:
        """Строка снимка из элемента контракта (без колонок экономики — у них свой цикл)."""
        return {
            "backend_id": backend_id,
            "user_id": item.id,
            "external_id": item.external_id,
            "is_paid": item.is_paid,
            "payments_count": item.payments_count,
            "renewals_count": item.renewals_count,
            "tokens": item.tokens,
            "subscription_active": item.subscription_active,
            "subscription_expires_at": item.subscription_expires_at,
            "plan_id": item.plan_id,
            "registered_at": item.registered_at,
        }

    @staticmethod
    def _parse_users_page(raw: dict[str, Any]) -> list[BackendUserItem]:
        """Элементы страницы `GET {P}/users`; ответ не по контракту → исключение цикла."""
        raw_items = raw.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("Бэк вернул данные не по контракту")
        return [
            BackendUserItem.model_validate(
                {
                    **item,
                    "backend_id": uuid.UUID(int=0),
                    "backend_code": "",
                    "backend_name": "",
                }
            )
            for item in raw_items
        ]

    @staticmethod
    def _parse_stats(raw: dict[str, Any]) -> BackendUsersStats:
        """Сводка `GET {P}/stats`; ответ не по контракту → исключение цикла."""
        return BackendUsersStats.model_validate(raw)


__all__ = [
    "PROVIDER_KEYS",
    "PROVIDER_OTHER",
    "BackendUsersSnapshotService",
    "normalize_provider",
]
