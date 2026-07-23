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
    backend_name: str
    message: str


class BackendUsersListResponse(BaseModel):
    """Ответ GET /api/backend-users: страница объединённого списка + сводка + сбои."""

    total: int
    items: list[BackendUserItem]
    stats: BackendUsersStats
    errors: list[BackendUsersSourceError] = Field(default_factory=list)


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
    endpoint: str
    prompt_preview: str | None = None
    status_code: int
    status: Literal["ok", "slow", "error"]
    duration_sec: float | None = None
    sent_at: datetime


class BackendUserRequestsResponse(BaseModel):
    total: int
    items: list[BackendUserRequest]


# --- Тарифы ---


class BackendProduct(BaseModel):
    product_id: str
    name: str
    price: str | None = None
    period: str | None = None


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
    """Ответ бэка на выдачу подписки (транзит). `applied=false` — повтор grant_id."""

    id: str
    tokens: float = 0
    subscription_active: bool = False
    subscription_expires_at: datetime | None = None
    applied: bool = True
