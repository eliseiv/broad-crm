"""Pydantic-контракты страницы «Пользователи бэков» (04-api.md#backend-users).

Данные приходят из внешних бэков по CRM Admin API contract v1
(docs/modules/backend-users/README.md); CRM только агрегирует и проксирует.
Ответ бэка валидируется этими схемами: поле не по контракту → 502
backend_admin_unavailable (сервис), а не 500. Необязательные блоки контракта
(revenue/media_stats) — Optional: бэк без экономики/медиа отдаёт null.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# --- Список пользователей ---


class BackendUserItem(BaseModel):
    """Строка таблицы пользователей. `backend_*` добавляет CRM при агрегации."""

    backend_id: uuid.UUID
    backend_code: str
    backend_name: str
    id: str
    external_id: str | None = None
    is_paid: bool = False
    payments_count: int = 0
    renewals_count: int = 0
    tokens: float = 0
    subscription_active: bool = False
    subscription_expires_at: datetime | None = None
    plan_id: str | None = None
    registered_at: datetime


class BackendUsersStats(BaseModel):
    """Сводка шапки списка. `cr_percent` считает CRM (paid/total)."""

    users_total: int = 0
    paid_users: int = 0
    payments_sum_usd: float = 0
    cr_percent: float = 0


class BackendUsersSourceError(BaseModel):
    """Бэк, не ответивший при агрегации «Все приложения» (partial-data warning в UI)."""

    backend_id: uuid.UUID
    backend_code: str
    backend_name: str
    message: str


class BackendUsersApiCosts(BaseModel):
    """Блок «Расходы API» — lifetime-агрегат по нормализованным провайдерам (ADR-080 §5).

    Источник — `revenue.providers` карточек `GET {P}/users/{id}`, собираемых воркером.
    Показатель **накопительный за всё время**: фильтр периода страницы на него НЕ
    действует (период меняет список и `stats`, но не расходы) — UI обязан назвать это
    подписью, иначе оператор прочтёт lifetime как «за выбранный период».

    `partial=true` ⇔ хотя бы у одного участвующего источника не завершён backfill
    карточек **ИЛИ** источник не отдаёт блок `revenue` (`revenue_supported IS FALSE`).
    Второй дизъюнкт обязателен: бэк уровня v1 покидает очередь backfill штатно, и без
    него неполная сумма объявлялась бы полной именно там, где это НИКОГДА не исправится
    само. UI обязан показывать признак.
    """

    openai_usd: float = 0
    anthropic_usd: float = 0
    fal_usd: float = 0
    other_usd: float = 0
    total_usd: float = 0
    partial: bool = False


class BackendUsersListResponse(BaseModel):
    """Ответ GET /api/backend-users: страница объединённого списка + сводка + сбои.

    `snapshot_at`/`api_costs` — **аддитивные** поля снимка (ADR-080 §6); существующие
    `total`/`items`/`stats`/`errors` не меняются. `null` в обоих — «снимок ещё не
    сформирован» (UI: «Снимок формируется…»).
    """

    total: int
    items: list[BackendUserItem]
    stats: BackendUsersStats
    errors: list[BackendUsersSourceError] = Field(default_factory=list)
    # MIN(refreshed_at) по участвующим источникам; хотя бы один ни разу не обновлялся → null.
    snapshot_at: datetime | None = None
    api_costs: BackendUsersApiCosts | None = None


# --- Карточка пользователя ---


class BackendUserBalance(BaseModel):
    tokens: float = 0
    credited_total: float | None = None
    spent_total: float | None = None


class BackendUserSubscription(BaseModel):
    plan_id: str | None = None
    plan_name: str | None = None
    price: str | None = None
    active: bool = False
    expires_at: datetime | None = None
    last_payment_at: datetime | None = None
    last_payment_method: str | None = None


class BackendUserRevenue(BaseModel):
    """Экономика пользователя; блок опционален по контракту (§4.5)."""

    income_usd: float = 0
    api_cost_usd: float = 0
    providers: dict[str, float] = Field(default_factory=dict)


class BackendUserMediaCounters(BaseModel):
    total: int = 0
    success: int = 0
    failed: int = 0


class BackendUserAvgGeneration(BaseModel):
    photo: float | None = None
    video: float | None = None
    overall: float | None = None


class BackendUserMediaStats(BaseModel):
    """Статистика генераций; блок опционален по контракту (§4.5)."""

    photos: BackendUserMediaCounters = Field(default_factory=BackendUserMediaCounters)
    videos: BackendUserMediaCounters = Field(default_factory=BackendUserMediaCounters)
    avg_generation_sec: BackendUserAvgGeneration = Field(default_factory=BackendUserAvgGeneration)


class BackendUserDetailResponse(BaseModel):
    """Ответ GET /api/backend-users/{backend_id}/users/{user_id}."""

    backend_id: uuid.UUID
    backend_code: str
    backend_name: str
    id: str
    external_id: str | None = None
    registered_at: datetime
    balance: BackendUserBalance = Field(default_factory=BackendUserBalance)
    subscription: BackendUserSubscription = Field(default_factory=BackendUserSubscription)
    revenue: BackendUserRevenue | None = None
    media_stats: BackendUserMediaStats | None = None


# --- История оплат / запросов ---


class BackendUserPayment(BaseModel):
    title: str
    description: str | None = None
    amount: float
    currency: str = "USD"
    status: Literal["success", "failed"]
    occurred_at: datetime


class BackendUserPaymentsResponse(BaseModel):
    total: int
    items: list[BackendUserPayment]


class BackendUserRequest(BaseModel):
    """Строка истории запросов. Поля экономики (v1.1) — ОПЦИОНАЛЬНЫ (ADR-072 §1.1).

    Бэк уровня v1 их не отдаёт, и это не ошибка: отсутствующее поле нормализуется в
    `null`, 502 из-за него не возникает. `null` у `tokens_spent`/`provider_cost_usd`
    означает «НЕ ИЗМЕРЕНО», а не ноль (ADR-072 §5): схлопывание в 0 на уровне строки
    запрещено. `refunded: true` — списание возвращено, при этом `tokens_spent`
    остаётся заполненным (возврат не обнуляет стоимость).
    """

    endpoint: str
    prompt_preview: str | None = None
    status_code: int
    status: Literal["ok", "slow", "error"]
    duration_sec: float | None = None
    sent_at: datetime
    tokens_spent: float | None = None
    provider_cost_usd: float | None = None
    # `true` — себестоимость выведена из тарифной пачки (оценка сверху); `false` — точное
    # значение; `null` — поле не отдано или себестоимости нет. Бэк уровня v1 поле не знает —
    # отсутствующее нормализуется в `null`, 502 из-за него не возникает.
    provider_cost_estimated: bool | None = None
    refunded: bool | None = None


class BackendUserRequestsResponse(BaseModel):
    total: int
    items: list[BackendUserRequest]


# --- Тарифы ---


class BackendProduct(BaseModel):
    """Тариф бэка для формы «Установить план».

    `archived` (contract v1.2, ADR-073 §5) транслируется, потому что форма архивные
    продукты **НЕ фильтрует** (выдать архивный план — законная операция; `archived` и
    `grantable` ортогональны), но обязана их **помечать**. `scope=grantable` МОЖЕТ
    вернуть архивные: сервер по `archived` не отбирает никогда — это поле, а не фильтр.
    Без трансляции признака пометить было бы нечем. Бэк без поддержки архива поля не
    отдаёт ⇒ `null` ⇒ помечать нечего, форма работает как прежде.
    """

    product_id: str
    name: str
    price: str | None = None
    period: str | None = None
    archived: bool | None = None


class BackendProductsResponse(BaseModel):
    items: list[BackendProduct]


# --- Admin-операции (запись) ---


class AddBackendUserTokensRequest(BaseModel):
    """Тело POST .../tokens. Отрицательное значение — списание (контракт §3.1); 0 запрещён."""

    amount: int = Field(..., ge=-1_000_000_000, le=1_000_000_000)


class GrantBackendUserSubscriptionRequest(BaseModel):
    """Тело POST .../subscription. `grant_id` — ключ идемпотентности, генерирует UI."""

    product_id: str = Field(..., min_length=1, max_length=255)
    expires_in_days: int = Field(..., gt=0, le=3660)
    grant_id: str = Field(..., min_length=1, max_length=255)


class BackendUserTokensResponse(BaseModel):
    """Ответ бэка на начисление токенов (транзит)."""

    id: str
    tokens: float


class BackendUserGrantResponse(BaseModel):
    """Ответ бэка на выдачу подписки (транзит). `applied=false` — повтор grant_id.

    `tokens` — **`None` = «НЕ ИЗМЕРЕНО», а не ноль** (нормативный принцип ADR-072 §5).
    Выдача плана баланс не меняет, поэтому бэк вправе поле не отдавать; дефолт `0`
    (прежняя редакция) превращал «поле отсутствует» в «на счету ноль», и best-effort
    touch снимка (ADR-080 §4) затирал реальный баланс нулём до следующего цикла воркера.
    У `BackendUserTokensResponse.tokens` поле обязательное — там начисление баланс
    меняет и ответ обязан его нести.
    """

    id: str
    tokens: float | None = None
    subscription_active: bool = False
    subscription_expires_at: datetime | None = None
    applied: bool = True
