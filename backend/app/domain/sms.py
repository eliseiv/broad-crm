"""Чистые доменные функции модуля «СМС» (modules/sms, ADR-030).

Без I/O, БД и сайд-эффектов — тестируются qa напрямую. Порт донорских
`domain/services.normalize_phone`, `application/cursor` и `telegram/init_data`.
Никогда не логируют секреты/PII (init_data не проходит через логи).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl

# --- Ролевая видимость SMS (scope) ------------------------------------------


@dataclass(frozen=True)
class SmsScope:
    """Ролевая видимость SMS/номеров (ADR-030 §6, 05-security.md).

    Супер-админ (`is_super_admin`) → видит всё (`team_ids` не используется). Не-админ
    → видимость по **текущей** принадлежности номера команде (`team_id ∈ team_ids`);
    вне scope: read/list → пусто, мутация → 403 (анти-энумерация). Вычисляется
    фабрикой `get_sms_scope` в `api/deps.py`; хранится здесь, чтобы разорвать цикл
    импорта deps ↔ сервисы SMS.
    """

    is_super_admin: bool
    team_ids: frozenset[uuid.UUID]


# --- Нормализация телефона (E.164) ------------------------------------------


def normalize_phone(value: str) -> str:
    """Приводит номер к E.164 (`+<digits>`). Пустой/мусорный вход → пустая строка."""
    digits = re.sub(r"[^\d+]", "", value or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = f"+{digits[2:]}"
    if not digits.startswith("+"):
        digits = f"+{digits}"
    return digits


# --- Keyset-курсор ленты сообщений ------------------------------------------

_CURSOR_SEP = "|"


class SmsCursorError(Exception):
    """Битый/недекодируемый keyset-курсор (сервис маппит в 400 invalid_cursor)."""


def encode_cursor(received_at: datetime, row_id: int) -> str:
    """Кодирует позицию `(received_at, id)` в opaque base64url-токен (без padding).

    `received_at` — tz-aware datetime; `isoformat`/`fromisoformat` round-трипят
    значение без потери точности. Фильтры курсор не несёт.
    """
    raw = f"{received_at.isoformat()}{_CURSOR_SEP}{row_id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    """Декодирует opaque-курсор в `(received_at, id)`.

    :raises SmsCursorError: токен пуст, недекодируем или структурно неверен
        (в т.ч. наивный datetime — позиция всегда кодируется из TIMESTAMPTZ).
    """
    if not cursor:
        raise SmsCursorError("Пустой курсор")
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        iso, id_str = raw.rsplit(_CURSOR_SEP, 1)
        received_at = datetime.fromisoformat(iso)
        row_id = int(id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SmsCursorError("Битый курсор пагинации") from exc
    if received_at.tzinfo is None:
        raise SmsCursorError("Курсор без таймзоны")
    return received_at, row_id


# --- Telegram WebApp initData (HMAC-SHA256 + TTL) ---------------------------

InitDataError = Literal[
    "malformed",
    "missing_hash",
    "missing_user",
    "invalid_user_payload",
    "missing_auth_date",
    "hash_mismatch",
    "expired",
]


@dataclass(frozen=True, slots=True)
class ValidatedInitData:
    """Проверенный payload initData (только нужные поля)."""

    telegram_user_id: int
    first_name: str | None
    username: str | None
    auth_date: int  # unix seconds


def _build_data_check_string(pairs: list[tuple[str, str]]) -> str:
    filtered = [(k, v) for k, v in pairs if k != "hash"]
    filtered.sort(key=lambda kv: kv[0])
    return "\n".join(f"{k}={v}" for k, v in filtered)


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def verify_init_data(
    raw: str, *, bot_token: str, max_age_seconds: int, now: int | None = None
) -> ValidatedInitData | InitDataError:
    """Валидирует `raw` initData c `bot_token`. Возвращает данные или литерал ошибки.

    HMAC-SHA256 по data-check-string (`WebAppData`-ключ из bot_token) + TTL
    `auth_date`. `now` инъектируется (тестируется без реального времени). Никогда
    не логирует `raw` (содержит подпись/PII).
    """
    if not raw or not bot_token:
        return "malformed"

    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return "malformed"
    if not pairs:
        return "malformed"

    keys_seen: set[str] = set()
    for k, _ in pairs:
        if k in keys_seen:
            return "malformed"
        keys_seen.add(k)

    submitted_hash: str | None = None
    user_field: str | None = None
    auth_date_field: str | None = None
    for k, v in pairs:
        if k == "hash":
            submitted_hash = v
        elif k == "user":
            user_field = v
        elif k == "auth_date":
            auth_date_field = v

    if not submitted_hash:
        return "missing_hash"
    if user_field is None:
        return "missing_user"
    if auth_date_field is None:
        return "missing_auth_date"

    try:
        auth_date = int(auth_date_field)
    except ValueError:
        return "missing_auth_date"

    data_check_string = _build_data_check_string(pairs)
    computed = hmac.new(
        _secret_key(bot_token),
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed, submitted_hash):
        return "hash_mismatch"

    current = int(now if now is not None else time.time())
    if current - auth_date > max_age_seconds:
        return "expired"

    try:
        user_payload = json.loads(user_field)
    except (json.JSONDecodeError, TypeError):
        return "invalid_user_payload"
    if not isinstance(user_payload, dict):
        return "invalid_user_payload"

    raw_id = user_payload.get("id")
    if not isinstance(raw_id, int) or isinstance(raw_id, bool):
        return "invalid_user_payload"

    first_name = user_payload.get("first_name")
    if not isinstance(first_name, str):
        first_name = None
    username = user_payload.get("username")
    if not isinstance(username, str):
        username = None

    return ValidatedInitData(
        telegram_user_id=int(raw_id),
        first_name=first_name,
        username=username,
        auth_date=auth_date,
    )


__all__ = [
    "InitDataError",
    "SmsCursorError",
    "SmsScope",
    "ValidatedInitData",
    "decode_cursor",
    "encode_cursor",
    "normalize_phone",
    "verify_init_data",
]
