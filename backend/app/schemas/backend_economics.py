"""Pydantic-контракты страницы «Продукты и тарифы» (04-api.md#backend-economics).

Данные приходят из внешних бэков по расширению v1.1 «экономика» CRM Admin API
(ADR-072, docs/modules/backend-economics/README.md); CRM — прокси без собственного
хранилища. Ответ бэка валидируется этими схемами: не по контракту → 502
backend_admin_unavailable (сервис), а не 500.

⚠️ **Асимметрия products ↔ pricing — намеренная** (ADR-072 §1.1 п.5):
`GET {P}/products` — путь **v1**, поэтому поля v1.1 (`tokens`, `avatar_tokens`,
`grantable`, `updated_at`) **nullable**: отсутствующее поле нормализуется в `null`, и
CRM **никогда** не отвечает 502 из-за его отсутствия. `GET {P}/pricing` существует
**только** в v1.1, поэтому `tariff_id`/`kind`/`tokens` его элемента **обязательны** —
200 без них = contract mismatch. Копировать опциональность продуктов на тарифы нельзя.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- Селектор приложения ---


class BackendEconomicsBackendItem(BaseModel):
    """Бэк с заданным Admin API Key — опция селектора приложения."""

    id: uuid.UUID
    code: str
    name: str


class BackendEconomicsBackendsResponse(BaseModel):
    """Ответ GET /api/backend-economics/backends (сортировка `name ASC`, tie-break `code`)."""

    items: list[BackendEconomicsBackendItem]


# --- Capabilities (конверт списков) ---


class BackendEconomicsLimits(BaseModel):
    """Границы клиентской валидации формы — ЗАМОРОЖЕН СОСТАВ КЛЮЧЕЙ (ADR-072 §7.2).

    ⚠️ Заморожены **только имена ключей и типы**; сами границы — **runtime-данные
    каждого бэка** (у разных бэков разные, меняются без изменения контракта). CRM их
    **не хардкодит**: отсутствующий ключ ⇒ `null` ⇒ клиентская проверка по нему не
    выполняется (полагаемся на 400 бэка). **Незнакомый ключ игнорируется** —
    forward-compatibility, это не contract mismatch и не 502.
    """

    model_config = ConfigDict(extra="ignore")

    product_tokens_max: int | None = None
    product_avatar_tokens_max: int | None = None
    tariff_tokens_max: float | None = None
    tariff_decimal_places: int | None = None


class BackendEconomicsCapabilities(BaseModel):
    """Ответ `GET {P}/capabilities` бэка (конверт списка, ADR-072 §7).

    `features` — единственный источник признака записи (`products.write_tokens` /
    `pricing.write_tokens`): наличие поля `tokens` признаком НЕ является, бэк вправе
    отдавать токены read-only. Список значений не сужается до `Literal` намеренно —
    незнакомая фича не должна ломать разбор ответа.

    ⛔ **Строгость схемы оправдана только там, где значение реально используется**
    (ADR-072 §7.2): поле `capabilities` без потребителя в CRM объявляется
    ОПЦИОНАЛЬНЫМ. `contract_version` и `cache_effective_after_seconds` CRM не
    показывает (задержку оператор видит из `effective_after_seconds` ответа `PATCH`),
    поэтому их обязательность превращала бы безобидное умолчание конформного бэка в
    `schema_mismatch` ⇒ `capabilities: null` ⇒ молча read-only страницу при любом
    праве. `limits` тоже nullable: «`limits` отсутствует целиком ⇒ клиентской
    валидации границ нет вовсе, форма остаётся работоспособной» (ADR-072 §7.2,
    04-api.md#backend-economics) — при обязательном поле это состояние было бы
    недостижимо и вырождалось бы в тот же молчаливый read-only.
    """

    contract_version: int | None = None
    features: list[str]
    limits: BackendEconomicsLimits | None = None
    cache_effective_after_seconds: int | None = None


# --- Продукты ---


class BackendEconomicsProduct(BaseModel):
    """Строка каталога продуктов. Поля v1.1 nullable — см. асимметрию в докстроке модуля.

    `tokens: null` = «бэк не отдал поле» ⇒ строка read-only (править нечего);
    `grantable: null` = «не отдано», а НЕ «не выдаётся»; `updated_at: null` = «ни разу
    не менялось» ⇒ `if_updated_at` в PATCH не отправляется.

    `archived` (contract v1.2, ADR-073 §1) — продукт скрыт с ВИТРИНЫ; на начисление и
    выдачу не влияет. `null`/отсутствует = **у бэка нет самого понятия архива** ⇒ все
    продукты считаются активными; это штатное состояние, а НЕ `schema_mismatch`.
    ⚠️ Здесь СОЗНАТЕЛЬНОЕ отличие от правила «`null` ≠ `false`» для `refunded`
    (ADR-072 §1.1): там неизвестность нельзя выдавать за отрицательное значение, потому
    что она видима оператору как факт о деньгах; здесь единственное осмысленное
    поведение витрины — показать всё.

    `price`/`period` остаются в схеме, хотя UI их колонки не рендерит (ADR-073 §2):
    контракт универсален, другой подключаемый бэк вправе их заполнять, а удаление поля
    было бы ломающим изменением ради нулевого выигрыша. Не удалять.
    """

    product_id: str
    name: str
    price: str | None = None
    period: str | None = None
    tokens: int | None = None
    avatar_tokens: int | None = None
    grantable: bool | None = None
    archived: bool | None = None
    updated_at: datetime | None = None


class BackendEconomicsProductsResponse(BaseModel):
    """Ответ GET /api/backend-economics/{backend_id}/products.

    `capabilities: null` = «фич НЕ подтверждено» — ЛЮБОЙ неуспех необязательного
    подзапроса (404, таймаут, 5xx, 401/403, битый JSON, ответ не по схеме), при этом
    список отдаётся 200 (ADR-072 §7.1).
    """

    items: list[BackendEconomicsProduct]
    capabilities: BackendEconomicsCapabilities | None = None


class BackendProductUpdateResponse(BackendEconomicsProduct):
    """Ответ PATCH продукта: элемент + дельта + окно применения у бэка.

    ⚠️ **Все три поля ОПЦИОНАЛЬНЫ** (ADR-073 §8) — схема, разбирающая ответ ПОСЛЕ
    необратимого side-effect, не должна быть строже модели данных (прецедент —
    ADR-057 §5: `200` без `smtp_message_id` перестал давать `502` после уже
    состоявшейся отправки). При archived-only правке контрагенту естественно опустить
    `previous_tokens` — токены не менялись; строгая схема превращала бы это в `502`
    поверх уже переключённого признака. У отсутствия каждого есть определённое
    безобидное поведение: нет `previous_tokens` ⇒ тост без дельты; нет `changed` ⇒
    трактуется как «изменилось» (сообщить о состоявшемся действии безопаснее, чем
    умолчать); нет `effective_after_seconds` ⇒ предложение о задержке опускается.

    ⚠️ Толерантность CRM **не снимает** обязанности контрагента присылать все три:
    это страховка от расхождения прочтений, а не разрешение опускать.
    """

    previous_tokens: int | None = None
    changed: bool | None = None
    effective_after_seconds: int | None = None


# --- Тарифы списания ---


class BackendEconomicsTariff(BaseModel):
    """Строка тарифов списания. `tariff_id`/`kind`/`tokens` ОБЯЗАТЕЛЬНЫ (ADR-072 §1.1 п.5).

    `tariff_id` — **opaque для CRM**: только ключ пути PATCH, интерпретировать его CRM
    не вправе. `kind` даёт разбивку «чат/фото/видео» без знания внутренних имён типов
    конкретного бэка; ключом остаётся `tariff_id`, потому что типов может быть больше.
    """

    tariff_id: str
    kind: Literal["chat", "photo", "video", "other"]
    name: str | None = None
    tokens: float
    updated_at: datetime | None = None


class BackendEconomicsPricingResponse(BaseModel):
    """Ответ GET /api/backend-economics/{backend_id}/pricing (конверт тот же, что у products)."""

    items: list[BackendEconomicsTariff]
    capabilities: BackendEconomicsCapabilities | None = None


class BackendTariffUpdateResponse(BackendEconomicsTariff):
    """Ответ PATCH тарифа: элемент + дельта + окно применения у бэка.

    Три поля опциональны СИММЕТРИЧНО ответу продукта (ADR-073 §8): расхождение схем
    двух PATCH'ей одного контракта было бы источником следующего сюрприза.
    """

    previous_tokens: float | None = None
    changed: bool | None = None
    effective_after_seconds: int | None = None


# --- Тела PATCH-запросов ---
#
# Верхних границ здесь НЕТ намеренно (ADR-072 §7.2): границы — runtime-данные бэка
# (`limits` ответа `/capabilities`), и константа в коде CRM молча блокировала бы
# легитимную правку. Нижняя граница `>= 0` и «число/целое» — собственная валидация
# CRM, она от `limits` не зависит и действует всегда.


class UpdateBackendProductRequest(BaseModel):
    """Тело PATCH продукта: хотя бы одно значимое поле, иначе 400 validation_error.

    Значимых полей ТРИ (contract v1.2, ADR-073 §1): `tokens`, `avatar_tokens` и
    `archived`. `if_updated_at` значимым не считается — это защита от «двух
    операторов», а не изменяемая величина.

    `archived` — булево, поэтому `False` («вернуть из архива») обязано доходить до
    бэка: отбор значимых полей идёт по `is not None`, а не по истинности.
    """

    tokens: int | None = Field(default=None, ge=0)
    avatar_tokens: int | None = Field(default=None, ge=0)
    archived: bool | None = None
    if_updated_at: datetime | None = None

    @model_validator(mode="after")
    def _require_meaningful_field(self) -> UpdateBackendProductRequest:
        if self.tokens is None and self.avatar_tokens is None and self.archived is None:
            raise ValueError("Укажите tokens, avatar_tokens и/или archived")
        return self


class UpdateBackendTariffRequest(BaseModel):
    """Тело PATCH тарифа списания. `tokens` — number (не обязательно целое)."""

    tokens: float = Field(..., ge=0)
    if_updated_at: datetime | None = None


__all__ = [
    "BackendEconomicsBackendItem",
    "BackendEconomicsBackendsResponse",
    "BackendEconomicsCapabilities",
    "BackendEconomicsLimits",
    "BackendEconomicsPricingResponse",
    "BackendEconomicsProduct",
    "BackendEconomicsProductsResponse",
    "BackendEconomicsTariff",
    "BackendProductUpdateResponse",
    "BackendTariffUpdateResponse",
    "UpdateBackendProductRequest",
    "UpdateBackendTariffRequest",
]
