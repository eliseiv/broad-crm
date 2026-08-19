"""Репозиторий Postgres-снимка «Юзеров бэков» (ADR-080 §2/§3, 03-data-model.md).

Две группы методов:

- **Запись (только воркер + best-effort touch, ADR-080 §4):** fingerprints одним SELECT,
  changed-only батч-upsert, `DELETE` отсутствующих (строго при полном обходе), очередь
  backfill, запись экономики карточек, состояние источника.
- **Чтение (read-path списка):** страница снимка с `JOIN backends`, `COUNT`, строки
  источников (сводка/`errors`/`snapshot_at`/`api_costs`).

Только `flush`/`execute` — транзакцией управляет вызывающий (воркер коммитит сам).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backend_user_snapshot import BackendUserSnapshot, BackendUserSnapshotSource
from app.models.service_backend import Backend

# Поля элемента `GET {P}/users`, зеркалируемые снимком. Порядок фиксирован — он же
# порядок fingerprint'а dirty-детекции (кортеж сравнивается целиком).
FINGERPRINT_FIELDS: tuple[str, ...] = (
    "external_id",
    "is_paid",
    "payments_count",
    "renewals_count",
    "tokens",
    "subscription_active",
    "subscription_expires_at",
    "plan_id",
    "registered_at",
)

Fingerprint = tuple[Any, ...]


def _as_utc(value: Any) -> Any:
    """Приводит `datetime` к aware-UTC; прочие значения возвращает как есть.

    **Обязательная нормализация fingerprint'а.** Из БД `TIMESTAMPTZ` приходит
    **aware**, а из ответа бэка `registered_at`/`subscription_expires_at` могут прийти
    **naive** (контракт требует UTC, но не обязывает суффикс `Z`). Наивный и aware
    `datetime` одного момента НЕ равны, поэтому без приведения fingerprint не совпадал бы
    НИКОГДА: changed-only-writes выродился бы в полную перезапись таблицы каждые 15 минут,
    а dirty-set — во всех пользователей, то есть в полный ре-fetch экономики каждый цикл.

    Naive-значение трактуется как UTC — ровно так же, как его трактует контракт бэка.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return value


def build_fingerprint(values: Mapping[str, Any]) -> Fingerprint:
    """Кортеж fingerprint'а из значений строки (порядок — `FINGERPRINT_FIELDS`).

    Единственная точка сборки для ОБЕИХ сторон сравнения (строка снимка из БД и элемент
    ответа бэка) — иначе нормализация разъехалась бы между ними, а именно на этом
    сравнении держится changed-only-writes.
    """
    return tuple(_as_utc(values.get(field_name)) for field_name in FINGERPRINT_FIELDS)


# Размер чанка для `DELETE ... WHERE user_id IN (...)`. Жёсткий потолок asyncpg —
# 32 767 bind-параметров на statement; 10 000 оставляет запас и на будущие предикаты.
# Вынесен константой, чтобы регресс-тест мог понизить порог и проверить сам чанкинг,
# не создавая 33 000 строк.
_DELETE_CHUNK = 10_000

# Escape-символ LIKE-шаблона поиска. Без экранирования `%`/`_` из пользовательского
# ввода превращаются в шаблонные метасимволы: `%` матчил бы ВСЕ строки, `_` — любой
# символ, то есть поиск молча возвращал бы не то, что ввёл оператор (норма ADR-080 §3 —
# подстрочный поиск по `user_id`/`external_id`, а не шаблонный).
_LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    """Экранирует метасимволы LIKE (`\\`, `%`, `_`) в подстроке поиска.

    Обратный слэш экранируется ПЕРВЫМ — иначе он продублировал бы escape-символы,
    добавленные следом.
    """
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", f"{_LIKE_ESCAPE}%")
        .replace("_", f"{_LIKE_ESCAPE}_")
    )


# `SUM` расходов по СЫРЫМ ключам провайдеров одного бэка (ADR-080 §2 п.6). Развёртка
# jsonb — `LATERAL jsonb_each_text`.
#
# Два защитных условия, оба обязательны — одна плохая строка не должна ронять пересчёт
# для ВСЕГО бэка:
#  - `jsonb_typeof(...) = 'object'` в аргументе LATERAL: `jsonb_each_text` на не-объекте
#    (jsonb-`null`, число, строка) поднимает ошибку, а фильтр `WHERE` к этому моменту
#    ещё не применён — LATERAL вычисляется раньше;
#  - регекс на `p.value`: нечисловое значение обрушило бы приведение `::float8`.
_SUM_PROVIDERS_SQL = text(
    """
SELECT p.key AS provider, SUM(p.value::float8) AS amount
FROM backend_user_snapshots s,
     LATERAL jsonb_each_text(
         CASE WHEN jsonb_typeof(s.api_cost_providers) = 'object'
              THEN s.api_cost_providers ELSE '{}'::jsonb END
     ) AS p(key, value)
WHERE s.backend_id = CAST(:backend_id AS uuid)
  AND p.value ~ '^-?[0-9]+(\\.[0-9]+)?([eE][-+]?[0-9]+)?$'
GROUP BY p.key
"""
)


@dataclass(frozen=True, slots=True)
class SnapshotSourceState:
    """Строка источника — то, что читает read-path (сводка, `errors`, свежесть, расходы)."""

    backend_id: uuid.UUID
    refreshed_at: datetime | None
    error_message: str | None
    stats_users_total: int
    stats_paid_users: int
    stats_payments_sum_usd: float
    api_costs: dict[str, float]
    revenue_backfill_done: bool
    revenue_supported: bool | None


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    """Строка страницы списка: снимок пользователя + денормализованные поля бэка."""

    backend_id: uuid.UUID
    backend_code: str
    backend_name: str
    user_id: str
    external_id: str | None
    is_paid: bool | None
    payments_count: int | None
    renewals_count: int | None
    tokens: float | None
    subscription_active: bool | None
    subscription_expires_at: datetime | None
    plan_id: str | None
    registered_at: datetime


class BackendUserSnapshotRepository:
    """Чтение/запись снимка пользователей бэков."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def session(self) -> AsyncSession:
        """Доступ к текущей сессии (транзакцией управляет вызывающий)."""
        return self._session

    # --- Запись (воркер) ---------------------------------------------------

    async def fingerprints(self, backend_id: uuid.UUID) -> dict[str, Fingerprint]:
        """`user_id → fingerprint` всех строк снимка бэка — ОДНИМ SELECT (без N+1).

        Основа changed-only-writes: строка переписывается, только если её кортеж
        отличается от пришедшего от бэка. Это и защита от churn таблицы (autovacuum), и
        бесплатная детекция активности для добора экономики (dirty-set, ADR-080 §5).
        """
        columns = [getattr(BackendUserSnapshot, field) for field in FINGERPRINT_FIELDS]
        stmt = select(BackendUserSnapshot.user_id, *columns).where(
            BackendUserSnapshot.backend_id == backend_id
        )
        rows = (await self._session.execute(stmt)).all()
        # Сборка — общим `build_fingerprint` (нормализация tz), тем же, что применяет
        # воркер к элементу ответа бэка: иначе стороны сравнения разъедутся.
        return {
            row[0]: build_fingerprint(dict(zip(FINGERPRINT_FIELDS, row[1:], strict=True)))
            for row in rows
        }

    async def upsert_rows(self, rows: list[dict[str, Any]]) -> None:
        """Батч-upsert строк снимка (`ON CONFLICT (backend_id, user_id) DO UPDATE`).

        PK-upsert гасит дубли, возникающие при сдвиге offset-пагинации источника во время
        обхода. Колонки экономики (`api_cost_*`, `revenue_refreshed_at`) НЕ трогаются:
        они живут своим циклом (§5), и обход списка не должен их обнулять.
        """
        if not rows:
            return
        stmt = pg_insert(BackendUserSnapshot).values(rows)
        update_columns = {field: getattr(stmt.excluded, field) for field in FINGERPRINT_FIELDS}
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    BackendUserSnapshot.backend_id,
                    BackendUserSnapshot.user_id,
                ],
                set_=update_columns,
            )
        )

    async def delete_rows(self, backend_id: uuid.UUID, user_ids: Iterable[str]) -> int:
        """Удаляет ПЕРЕЧИСЛЕННЫЕ строки снимка чанками. Возвращает число удалённых.

        Вызывать ТОЛЬКО после полностью успешного обхода (ADR-080 §2 п.3): оборванный
        обход снимок не прореживает.

        **Почему список УДАЛЯЕМЫХ, а не `NOT IN (все увиденные)`.** Прежняя редакция
        строила `user_id NOT IN :present` из всех id источника: на бэке с ~305 000
        пользователей это 305 000 bind-параметров против жёсткого потолка asyncpg
        (32 767) ⇒ исключение КАЖДЫЙ цикл, `refreshed_at` не проставлялся бы никогда, а
        страница навсегда осталась бы в состоянии «Снимок формируется…».

        Разность считается в Python (`fingerprints` уже держит все id снимка в памяти),
        поэтому в SQL уходит только то, что реально удаляется — в установившемся режиме
        это ноль строк. Чанк `_DELETE_CHUNK` держит число bind-параметров заведомо ниже
        потолка даже при разовой массовой чистке.

        **Маркер `seen_at` с батчевым «прикосновением» к каждой строке отвергнут:** он
        переписывал бы ВСЕ строки снимка каждые 15 минут и тем самым отменял инвариант
        changed-only-writes (ADR-080, 03-data-model.md §«Инварианты записи» п.2), ради
        которого снимок и пишется выборочно — цена (полная перезапись таблицы и нагрузка
        на autovacuum) несопоставима с задачей «удалить единицы исчезнувших строк».
        """
        ids = list(dict.fromkeys(user_ids))
        if not ids:
            return 0
        deleted = 0
        for start in range(0, len(ids), _DELETE_CHUNK):
            chunk = ids[start : start + _DELETE_CHUNK]
            result = await self._session.execute(
                delete(BackendUserSnapshot).where(
                    BackendUserSnapshot.backend_id == backend_id,
                    BackendUserSnapshot.user_id.in_(chunk),
                )
            )
            # Result.rowcount не типизирован в SQLAlchemy 2.x для async-результата.
            deleted += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return deleted

    async def backfill_candidates(self, backend_id: uuid.UUID, limit: int) -> list[str]:
        """Очередь холодного старта: `revenue_refreshed_at IS NULL`, `registered_at DESC`.

        Свежие пользователи ценнее — их расходы оператор смотрит чаще. Отдельного индекса
        по `revenue_refreshed_at` нет намеренно: очередь читается тем же
        `(backend_id, registered_at DESC)` с предикатом.
        """
        if limit <= 0:
            return []
        stmt = (
            select(BackendUserSnapshot.user_id)
            .where(
                BackendUserSnapshot.backend_id == backend_id,
                BackendUserSnapshot.revenue_refreshed_at.is_(None),
            )
            .order_by(BackendUserSnapshot.registered_at.desc(), BackendUserSnapshot.user_id)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_pending_revenue(self, backend_id: uuid.UUID) -> int:
        """Сколько строк снимка ещё ждут добора карточки (`revenue_refreshed_at IS NULL`)."""
        stmt = select(func.count()).where(
            BackendUserSnapshot.backend_id == backend_id,
            BackendUserSnapshot.revenue_refreshed_at.is_(None),
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def set_revenue(
        self,
        *,
        backend_id: uuid.UUID,
        user_id: str,
        api_cost_usd: float | None,
        providers: dict[str, float] | None,
        refreshed_at: datetime,
    ) -> None:
        """Пишет экономику одной карточки. `providers` — **сырые** ключи бэка.

        Нормализация провайдеров выполняется при агрегации, а не здесь: сохранённые сырые
        значения позволяют сменить правило нормализации без повторного обхода. Метка
        `revenue_refreshed_at` ставится и при отсутствии блока `revenue` (`api_cost_usd
        IS NULL`) — перечитывать такую строку бессмысленно; полноту суммы описывает
        `revenue_supported` источника, а не эта метка (ADR-080 §5).
        """
        stmt = (
            update(BackendUserSnapshot)
            .where(
                BackendUserSnapshot.backend_id == backend_id,
                BackendUserSnapshot.user_id == user_id,
            )
            .values(
                api_cost_usd=api_cost_usd,
                api_cost_providers=providers,
                revenue_refreshed_at=refreshed_at,
            )
        )
        await self._session.execute(stmt)

    async def sum_providers(self, backend_id: uuid.UUID) -> dict[str, float]:
        """`SUM` расходов по СЫРЫМ ключам провайдеров (`jsonb_each_text`) одного бэка.

        Нормализацию ключей выполняет вызывающий (`_normalize_provider`): агрегат по
        сырым ключам делает правило нормализации сменяемым без повторного обхода.
        """
        rows = (
            await self._session.execute(_SUM_PROVIDERS_SQL, {"backend_id": str(backend_id)})
        ).all()
        return {str(provider): float(amount or 0) for provider, amount in rows}

    async def touch_row(
        self,
        *,
        backend_id: uuid.UUID,
        user_id: str,
        tokens: float | None = None,
        subscription_active: bool | None = None,
        subscription_expires_at: datetime | None = None,
        touch_subscription: bool = False,
    ) -> None:
        """Best-effort обновление строки снимка после admin-мутации (ADR-080 §4).

        Обновляются ТОЛЬКО переданные величины (значения из ответа бэка). Строки в снимке
        может ещё не быть (первый цикл воркера не прошёл) — тогда `UPDATE` затрагивает 0
        строк, и это не ошибка: снимок наполнится штатно.
        """
        values: dict[str, Any] = {}
        if tokens is not None:
            values["tokens"] = tokens
        if touch_subscription:
            values["subscription_active"] = subscription_active
            values["subscription_expires_at"] = subscription_expires_at
        if not values:
            return
        await self._session.execute(
            update(BackendUserSnapshot)
            .where(
                BackendUserSnapshot.backend_id == backend_id,
                BackendUserSnapshot.user_id == user_id,
            )
            .values(**values)
        )

    # --- Состояние источника ------------------------------------------------

    async def upsert_source(self, backend_id: uuid.UUID, values: dict[str, Any]) -> None:
        """Идемпотентный upsert строки источника (создаётся при первом цикле бэка)."""
        stmt = pg_insert(BackendUserSnapshotSource).values(backend_id=backend_id, **values)
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[BackendUserSnapshotSource.backend_id],
                set_={**values, "updated_at": func.now()},
            )
        )

    async def source_states(
        self, backend_ids: list[uuid.UUID] | None = None
    ) -> list[SnapshotSourceState]:
        """Строки источников (все либо перечисленные) — вход сводки/`errors`/расходов."""
        stmt = select(
            BackendUserSnapshotSource.backend_id,
            BackendUserSnapshotSource.refreshed_at,
            BackendUserSnapshotSource.error_message,
            BackendUserSnapshotSource.stats_users_total,
            BackendUserSnapshotSource.stats_paid_users,
            BackendUserSnapshotSource.stats_payments_sum_usd,
            BackendUserSnapshotSource.api_costs,
            BackendUserSnapshotSource.revenue_backfill_done,
            BackendUserSnapshotSource.revenue_supported,
        )
        if backend_ids is not None:
            if not backend_ids:
                return []
            stmt = stmt.where(BackendUserSnapshotSource.backend_id.in_(backend_ids))
        rows = (await self._session.execute(stmt)).all()
        return [
            SnapshotSourceState(
                backend_id=row[0],
                refreshed_at=row[1],
                error_message=row[2],
                stats_users_total=int(row[3] or 0),
                stats_paid_users=int(row[4] or 0),
                stats_payments_sum_usd=float(row[5] or 0),
                api_costs={str(k): float(v or 0) for k, v in dict(row[6] or {}).items()},
                revenue_backfill_done=bool(row[7]),
                revenue_supported=row[8],
            )
            for row in rows
        ]

    # --- Чтение (read-path списка) -----------------------------------------

    async def list_page(
        self,
        *,
        backend_ids: list[uuid.UUID],
        search: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        is_paid: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[list[SnapshotRow], int]:
        """Страница списка + `total` одним индексируемым `ORDER BY` (ADR-080 §3).

        Сортировка — `registered_at DESC, backend_id, user_id`: tie-break обязателен,
        иначе `LIMIT/OFFSET` даёт нестабильные страницы (окно merge ≤ 1000 упразднено —
        глубина ничем не ограничена). Поиск — подстрочный регистронезависимый по
        `user_id` и `external_id`: **ровно** тот набор полей, по которому искал бэк
        (расширять нельзя — других полей в снимке нет, сужать — тоже).
        """
        if not backend_ids:
            return [], 0

        base = (
            select(
                BackendUserSnapshot.backend_id,
                Backend.code,
                Backend.name,
                BackendUserSnapshot.user_id,
                BackendUserSnapshot.external_id,
                BackendUserSnapshot.is_paid,
                BackendUserSnapshot.payments_count,
                BackendUserSnapshot.renewals_count,
                BackendUserSnapshot.tokens,
                BackendUserSnapshot.subscription_active,
                BackendUserSnapshot.subscription_expires_at,
                BackendUserSnapshot.plan_id,
                BackendUserSnapshot.registered_at,
            )
            .join(Backend, Backend.id == BackendUserSnapshot.backend_id)
            .where(BackendUserSnapshot.backend_id.in_(backend_ids))
        )
        base = self._apply_filters(base, search, date_from, date_to, is_paid)

        count_stmt = (
            select(func.count())
            .select_from(BackendUserSnapshot)
            .where(BackendUserSnapshot.backend_id.in_(backend_ids))
        )
        count_stmt = self._apply_filters(count_stmt, search, date_from, date_to, is_paid)
        total = int((await self._session.execute(count_stmt)).scalar_one())

        page = (
            base.order_by(
                BackendUserSnapshot.registered_at.desc(),
                BackendUserSnapshot.backend_id,
                BackendUserSnapshot.user_id,
            )
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(page)).all()
        return [
            SnapshotRow(
                backend_id=row[0],
                backend_code=row[1],
                backend_name=row[2],
                user_id=row[3],
                external_id=row[4],
                is_paid=row[5],
                payments_count=row[6],
                renewals_count=row[7],
                tokens=row[8],
                subscription_active=row[9],
                subscription_expires_at=row[10],
                plan_id=row[11],
                registered_at=row[12],
            )
            for row in rows
        ], total

    @staticmethod
    def _apply_filters(
        stmt: Select[Any],
        search: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        is_paid: bool | None,
    ) -> Select[Any]:
        """Общие предикаты страницы и `COUNT` (держать в одном месте — иначе разъедутся)."""
        if search:
            pattern = f"%{_escape_like(search)}%"
            stmt = stmt.where(
                BackendUserSnapshot.user_id.ilike(pattern, escape=_LIKE_ESCAPE)
                | BackendUserSnapshot.external_id.ilike(pattern, escape=_LIKE_ESCAPE)
            )
        if date_from is not None:
            stmt = stmt.where(BackendUserSnapshot.registered_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(BackendUserSnapshot.registered_at <= date_to)
        if is_paid is not None:
            stmt = stmt.where(BackendUserSnapshot.is_paid.is_(is_paid))
        return stmt


__all__ = [
    "FINGERPRINT_FIELDS",
    "BackendUserSnapshotRepository",
    "Fingerprint",
    "build_fingerprint",
    "SnapshotRow",
    "SnapshotSourceState",
]
