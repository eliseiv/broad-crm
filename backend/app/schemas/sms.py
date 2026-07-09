"""Схемы модуля «СМС» (04-api.md#sms, ADR-030).

Секреты (Twilio/Telegram-токены) в схемах не фигурируют. `SmsNumberItem.label` —
системное поле (зеркало Twilio `friendly_name`), редактированию через API не
подлежит. `SmsNumberUpdateRequest` — presence-семантика затирания через
`model_fields_set` (роутер/сервис различает «поле отсутствует» от «передано пустым»).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SmsTeamRef(BaseModel):
    """Ссылка на CRM-команду (id + текущее название)."""

    id: uuid.UUID
    name: str


class SmsNumberRef(BaseModel):
    """Ссылка на текущий номер (по `to_number` сообщения); `null`, если номер удалён."""

    id: int
    phone_number: str
    team: SmsTeamRef | None
    login: str | None
    app_name: str | None
    note: str | None


class SmsNumberItem(BaseModel):
    """Элемент списка номеров и тело 200 PATCH/transfer (04-api.md#схема-smsnumberitem)."""

    id: int
    phone_number: str
    label: str | None
    team: SmsTeamRef | None
    login: str | None
    app_name: str | None
    note: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SmsNumbersResponse(BaseModel):
    """Ответ 200 GET /api/sms/numbers."""

    numbers: list[SmsNumberItem]


class SmsMessageItem(BaseModel):
    """Элемент ленты входящих SMS (04-api.md#схема-smsmessageitem)."""

    id: int
    from_number: str
    to_number: str
    body: str
    received_at: datetime
    number: SmsNumberRef | None


class SmsMessagesResponse(BaseModel):
    """Ответ 200 GET /api/sms/messages (страница + opaque keyset-курсор)."""

    messages: list[SmsMessageItem]
    next_cursor: str | None


class SmsNumberUpdateRequest(BaseModel):
    """Тело PATCH /api/sms/numbers/{id} (presence-семантика, 04-api.md).

    Все поля опциональны; «переданное поле» — по `model_fields_set`. Значение
    (после `strip`) непустое → установить; пустое/пробельное или `null` → затереть
    (`NULL`); поле отсутствует → не меняется. `max_length=200` (превышение →
    400 validation_error через обработчик RequestValidationError).
    """

    login: str | None = Field(default=None, max_length=200)
    app_name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=200)


class SmsNumberTransferRequest(BaseModel):
    """Тело POST /api/sms/numbers/{id}/transfer (04-api.md).

    `team_id=null` → снять команду (unassigned); иначе привязать к существующей
    команде (несуществующая → 404 sms_team_not_found).
    """

    team_id: uuid.UUID | None = None


class SmsSyncResult(BaseModel):
    """Ответ 200 POST /api/sms/numbers/sync (04-api.md)."""

    synced_total: int
    added: int
    skipped_existing: int


class TelegramLinkRequest(BaseModel):
    """Тело POST /api/sms/telegram/link (Mini App-привязка под JWT)."""

    init_data: str


class TelegramLinkResponse(BaseModel):
    """Ответ 200 POST /api/sms/telegram/link."""

    linked: bool
    telegram_user_id: int


class TelegramAuthRequest(BaseModel):
    """Тело POST /api/sms/telegram/auth (публичный беспарольный Telegram-SSO, HMAC)."""

    init_data: str


class TelegramAuthResponse(BaseModel):
    """Ответ 200 POST /api/sms/telegram/auth — беспарольный Telegram-SSO (ADR-031).

    Выдаёт CRM access-JWT (как `POST /api/auth/login`): `access_token` c `sub`=
    `users.username` резолвнутого оператора, `uid`/`role`/`superadmin:false`;
    `expires_in` — TTL access-токена в секундах; `linked` всегда `true` при успехе
    (линк upserted/revived на этот `telegram_user_id`). Контракт —
    04-api.md#post-apismstelegramauth.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    telegram_user_id: int
    linked: bool


class TeamNumberItem(BaseModel):
    """Элемент GET /api/teams/{id}/numbers (04-api.md, ADR-030 §8 + ADR-034).

    Схема под гейт `teams:view`: состав номеров + ссылка на команду + слабо-
    чувствительный идентифицирующий контекст `login`/`app_name` (ADR-034). БЕЗ
    `note`/`label` — они доступны лишь на эндпоинтах страницы «СМС» под матрицей
    `sms:*`. `team` — всегда запрошенная команда `{id}` (номера отфильтрованы по
    `team_id`), потому не опционален.
    """

    id: int
    phone_number: str
    team: SmsTeamRef
    login: str | None
    app_name: str | None


class TeamNumbersResponse(BaseModel):
    """Ответ 200 GET /api/teams/{id}/numbers (detail-панель /teams, ADR-030)."""

    numbers: list[TeamNumberItem]
