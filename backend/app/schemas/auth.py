"""Схемы аутентификации (04-api.md#auth, ADR-025)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.user import TeamRef


class LoginRequest(BaseModel):
    """Тело POST /api/auth/login.

    `username` — **идентификатор входа** (логин ИЛИ телеграм-ник, ADR-025; имя поля
    сохранено для совместимости). `password` — **опционален**: для парольного
    пользователя обязателен по смыслу (пустой → 401), для беспарольного игнорируется
    (ответ `password_setup_required`). Длина пароля намеренно не ограничена снизу
    (пустой/отсутствующий трактуется как «без пароля» → 401, а не 400).
    """

    username: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, max_length=256)


class SetPasswordRequest(BaseModel):
    """Тело POST /api/auth/set-password (установка пароля «первого входа», ADR-025).

    `password` required; длина 8–128 валидируется сервисом → 422 unprocessable
    (слабый/короткий пароль), а не 400 (schema-level без границы длины).
    """

    password: str


class LoginResponse(BaseModel):
    """Ответ 200 POST /api/auth/login и POST /api/auth/set-password (ADR-025).

    Дискриминирован по `password_setup_required`:
      - `false` — обычный вход: заполнен `access_token` (`setup_token=None`);
      - `true` — требуется установка пароля: заполнен `setup_token` (`access_token=None`).
    `None`-поля исключаются из тела ответа (`response_model_exclude_none=True` на роуте).
    """

    password_setup_required: bool
    access_token: str | None = None
    setup_token: str | None = None
    token_type: str = "bearer"
    expires_in: int


class TokenResponse(BaseModel):
    """Успешный ответ входа (обычный access-токен). Внутренний хелпер построения
    `LoginResponse` при `password_setup_required=false`."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeResponse(BaseModel):
    """Ответ 200 GET /api/auth/me (профиль + права + scope каналов, ADR-021, ADR-055 §5.1).

    `permissions` — производное для UI-гейтинга (для супер-админа — полный каталог).
    Безопасность обеспечивается сервером (403), UI-гейтинг — только UX.
    `sees_all_sms_teams` — производный admin-уровень видимости SMS (ADR-032/036):
    `is_superadmin OR permissions_subset(full_catalog_permissions(), permissions)`;
    backend — единственный источник (фронт не дублирует `permissions_subset`).

    `mail_teams`/`sms_teams` (ADR-055 §5.1) — **ЭФФЕКТИВНЫЙ** scope канала: у не-админа
    `user_teams` ∪ доп-команды канала (объединение, НЕ только добавка), у **admin-уровня —
    ВСЕ команды системы** (`[]` не отдаётся: иначе фильтр «Команда» в Mini App у него
    остался бы пустым — `GET /api/teams` оттуда запрещён). Это делает `/me` ЕДИНСТВЕННЫМ
    источником опций команд канала на клиенте (закрытый TD-058/TD-050).
    Не путать с `mail_extra_teams`/`sms_extra_teams` users CRUD — там ТОЛЬКО хранимая
    добавка (§5.2, имена разведены намеренно).
    """

    username: str
    role: str
    is_superadmin: bool
    permissions: dict[str, list[str]]
    sees_all_sms_teams: bool
    # Производный admin-уровень видимости почты (ADR-038 §3): тот же предикат, что
    # backend `get_mail_scope`. Фильтр «Команда» по нему НЕ гейтится (ADR-055 §6.2).
    sees_all_mail_teams: bool
    mail_teams: list[TeamRef]
    sms_teams: list[TeamRef]
    # Видит ли актор объекты без команды этого канала (`team_id IS NULL`), ADR-055 §3.
    # Значение = `users.<channel>_includes_unassigned`; при `sees_all_<channel>_teams` → true.
    mail_includes_unassigned: bool
    sms_includes_unassigned: bool
