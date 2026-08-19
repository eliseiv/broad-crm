"""Схемы реестра пользователей (04-api.md#users, ADR-021/022/025).

Пароль (plaintext) НИКОГДА не присутствует в ответах — только на вход. `username`
валидируется сервисом (кириллица-допускающий формат) → 422 unprocessable; поэтому
в схеме — простой `str` (без format-констрейнтов, чтобы не давать преждевременный
400 вместо нормативного 422). `telegram` (опц., ADR-025; заменяет прежний `email`)
валидируется сервисом → 422. `team_ids` (опц., CRM-команды) — существование проверяет
сервис → 422. `password` **опционален** (беспарольный пользователь, ADR-025); длина
8–128 при наличии валидируется сервисом → 422.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TeamRef(BaseModel):
    """Ссылка на CRM-команду пользователя (id + название) для списка `teams`."""

    id: uuid.UUID
    name: str


class RoleRef(BaseModel):
    """Ссылка на роль пользователя (id + название) для списка `roles` (ADR-079 §1).

    Компактная форма по образцу `TeamRef`; заменяет прежнюю пару `role_id`/`role_name`.
    """

    id: uuid.UUID
    name: str


class UserCreateRequest(BaseModel):
    """Тело POST /api/users (04-api.md#post-apiusers, ADR-079 §7/§8/§9).

    **`username` в запросе НЕТ**: сервис выставляет его сам —
    `username := normalize_telegram(telegram)` (§9). Поле «Логин» удалено из UI.

    `last_name`/`first_name` — **обязательны** (формат — `validate_name_part`, 422);
    `middle_name` опционально. `telegram` — **обязателен** (422 при отсутствии/пустоте).
    `password` **опционален** (беспарольный пользователь, ADR-025); длина 8–128 при
    наличии валидируется сервисом (422). `role_ids` — **непустой** список существующих
    ролей (422 иначе; «минимум одна роль» — инвариант сервиса, не БД). `team_ids` —
    существование проверяет сервис (422).

    `*_extra_team_ids` (ADR-055 §5.2) — **дополнительные** команды канала сверх базового
    членства; существование — сервис (422, `details[].field` = имя поля). Пересечение с
    `team_ids` того же запроса сервис **вычитает** (инвариант §2.3) — это НЕ ошибка.
    `*_extra_includes_unassigned` — «Без команды» канала (default `false`).
    """

    last_name: str
    first_name: str
    middle_name: str | None = None
    telegram: str
    password: str | None = None
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    mail_extra_team_ids: list[uuid.UUID] = Field(default_factory=list)
    mail_extra_includes_unassigned: bool = False
    sms_extra_team_ids: list[uuid.UUID] = Field(default_factory=list)
    sms_extra_includes_unassigned: bool = False


class UserUpdateRequest(BaseModel):
    """Тело PATCH /api/users/{id} (04-api.md#patch-apiusersid). Все поля опц.

    «Переданное поле» определяется по `model_fields_set` (Pydantic v2 `exclude_unset`).
    `username` не редактируется **и не пересчитывается** при смене `telegram` (ADR-079
    §9 — иначе поменялся бы `sub` уже выпущенных токенов).

    ФИО (ADR-079 §7): `last_name`/`first_name` не передано → не менять, передано —
    валидный формат обязателен, `null`/`""` → **422** (очистка обязательной части ФИО не
    предусмотрена). `middle_name` — единственная часть, которую можно снять
    (`null`/`""` → `NULL`).

    `telegram`: не передано → не менять; валидный → установить; ⛔ `null`/`""` →
    **422** (ADR-079 §8 — **очистка запрещена**: поля «Логин» в UI нет, пользователь
    остался бы без единого способа входа; прежняя норма «убрать телеграм» отменена).

    `role_ids`: не передано → роли не менять; передано → **полностью заменяет** набор;
    `[]` → **422** («минимум одна роль»). `password` без schema-констрейнта длины:
    пустая строка `""` и длина вне 8–128 валидируются сервисом → 422 unprocessable
    (`""` — не «очистка»). `team_ids`: передано → полностью заменяет набор CRM-команд;
    при исключении из команды, которую пользователь ведёт, лидерство авто-передаётся
    (ADR-026).

    `*_extra_team_ids` (ADR-055 §5.2): не передано → добавку канала не менять; передано →
    **полностью заменяет** её (`[]` → снять все). Пересечение с эффективным базовым
    набором (`team_ids` этого запроса, иначе — текущее членство) **вычитается** (§2.3).
    Следствие: добавление команды в `team_ids` снимает её копию из добавки, а исключение
    из команды не оставляет «висящего» доступа. `*_extra_includes_unassigned`: не
    передано → не менять.
    """

    last_name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    telegram: str | None = None
    role_ids: list[uuid.UUID] | None = None
    is_active: bool | None = None
    password: str | None = None
    team_ids: list[uuid.UUID] | None = None
    mail_extra_team_ids: list[uuid.UUID] | None = None
    mail_extra_includes_unassigned: bool | None = None
    sms_extra_team_ids: list[uuid.UUID] | None = None
    sms_extra_includes_unassigned: bool | None = None


class UserListItem(BaseModel):
    """Элемент GET /api/users и тело 201 POST / 200 PATCH (04-api.md#users).

    Пароль (`password`/`password_hash`) в ответах отсутствует всегда — только
    производный `has_password`. `status` — производный тристатус (ADR-028): `is_active`
    приоритетен (`false` → `"inactive"`), затем факт первого входа (`first_login_at`);
    сама метка `first_login_at` наружу не отдаётся. `teams` — CRM-команды пользователя
    (ADR-022), не mail-«команды».

    `mail_extra_teams`/`sms_extra_teams` (ADR-055 §5.2) — **ТОЛЬКО ДОБАВКА** канала
    (строки `user_channel_teams`), **без** базовых `teams`: то, что реально хранится.
    Эффективный scope канала = `teams ∪ <channel>_extra_teams` (его в готовом виде отдаёт
    `GET /api/auth/me` — имена полей разведены намеренно).
    `bot_started` — есть хотя бы одна активная строка `knowledge_bot_links` (ADR-076).
    """

    id: uuid.UUID
    # Скрытый технический логин (ADR-079 §9): в запросах его нет, но в ответе он
    # остаётся — как фолбэк-отображение для строк без ФИО и как диагностическое значение.
    username: str
    # ФИО (ADR-079 §7). `null` — часть не заполнена; отображаемое имя строит клиент
    # («{last_name} {first_name} {middle_name}» со схлопыванием пустых, всё пусто →
    # `username`).
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    telegram: str | None
    has_password: bool
    # ВСЕ роли пользователя (ADR-079 §1), порядок — `user_roles.created_at ASC, role_id`.
    # Заменяет удалённые `role_id`/`role_name`.
    roles: list[RoleRef]
    is_active: bool
    status: Literal["pending", "active", "inactive"]
    teams: list[TeamRef]
    mail_extra_teams: list[TeamRef]
    mail_extra_includes_unassigned: bool
    sms_extra_teams: list[TeamRef]
    sms_extra_includes_unassigned: bool
    bot_started: bool
    created_at: datetime
    updated_at: datetime


class UserListResponse(BaseModel):
    """Ответ 200 GET /api/users."""

    items: list[UserListItem]
