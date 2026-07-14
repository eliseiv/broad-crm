"""Чистые доменные типы модуля «Почты» (modules/mail, ADR-044).

Без I/O, БД и сайд-эффектов. `MailScope` вынесен сюда (рядом с `SmsScope` в
`domain/sms.py`), чтобы разорвать цикл импорта `api/deps.py` ↔ сервис почты и
тестироваться qa напрямую. Здесь же — компаундный keyset-курсор ленты писем
`(internal_date, id)` (ADR-044 §2, MINOR-2) и нормы валидации reply (ADR-044 §8).
"""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MailScope:
    """Ролевая видимость почты по CRM-командам (ADR-044 §7 в редакции ADR-055 §3).

    `sees_all_teams` — «видит все команды» ⇔ `is_superadmin` ИЛИ роль владеет полным
    каталогом прав (ADR-032/038; предикат ADR-055 НЕ менял). При True → доступ ко всем
    ящикам/письмам, включая бесхозные (`team_ids`/`includes_unassigned` не используются).

    Иначе (ADR-055 §3):
    - `team_ids` = `user_teams` **∪** доп-команды канала `mail` (`user_channel_teams`);
    - `includes_unassigned` = `users.mail_includes_unassigned` — «Без команды»: доступ к
      ящикам с `team_id IS NULL` (просмотр **и** правка/синк/удаление наравне со своей
      командой; создание ящика с `team_id=null` и перенос ящика — по-прежнему admin-only).

    **Единый предикат (применять ВЕЗДЕ — и на чтении, и на мутации):**
    `account.team_id IN team_ids` **OR** (`includes_unassigned` **AND** `team_id IS NULL`)
    — см. `matches`. Прямое `account.team_id in scope.team_ids` без ветки
    `includes_unassigned` в новом коде = **дефект** (ADR-055 «Последствия»).

    Вне scope: чтение → пусто (анти-энумерация), мутация → 403. Пустой `team_ids` **и**
    `includes_unassigned=false` у не-админа → пустая страница без выборки.
    """

    sees_all_teams: bool
    team_ids: frozenset[uuid.UUID]
    includes_unassigned: bool = False

    def matches(self, team_id: uuid.UUID | None) -> bool:
        """Единый предикат scope (ADR-055 §3) для объекта с командой `team_id`."""
        if self.sees_all_teams:
            return True
        if team_id is None:
            return self.includes_unassigned
        return team_id in self.team_ids

    @property
    def is_empty(self) -> bool:
        """Не-админ без единой команды И без «Без команды» → пусто БЕЗ выборки (§3)."""
        return not self.sees_all_teams and not self.team_ids and not self.includes_unassigned


# --- Компаундный keyset-курсор ленты писем (ADR-044 §2, MINOR-2) -------------

_CURSOR_SEP = "|"


class MailCursorError(Exception):
    """Битый/недекодируемый keyset-курсор (сервис маппит в 400 invalid_cursor)."""


def encode_mail_cursor(internal_date: datetime, row_id: int) -> str:
    """Кодирует позицию `(internal_date, id)` в opaque base64url-токен (без padding).

    Компаундность обязательна: `internal_date` не уникален (массовая рассылка приходит
    одной секундой) → пагинация по одной дате даёт пропуски/дубли на границах (ADR-044
    §2). `internal_date` — tz-aware; `isoformat`/`fromisoformat` round-трипят без потери.
    """
    raw = f"{internal_date.isoformat()}{_CURSOR_SEP}{row_id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_mail_cursor(cursor: str) -> tuple[datetime, int]:
    """Декодирует opaque-курсор в `(internal_date, id)`.

    :raises MailCursorError: токен пуст, недекодируем или структурно неверен (в т.ч.
        наивный datetime — позиция всегда кодируется из TIMESTAMPTZ).
    """
    if not cursor:
        raise MailCursorError("Пустой курсор")
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        iso, id_str = raw.rsplit(_CURSOR_SEP, 1)
        internal_date = datetime.fromisoformat(iso)
        row_id = int(id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MailCursorError("Битый курсор пагинации") from exc
    if internal_date.tzinfo is None:
        raise MailCursorError("Курсор без таймзоны")
    return internal_date, row_id


# --- Нормы валидации reply (ADR-044 §8, перенос из снятого ADR-0035) ---------

# Прагматичный e-mail-формат (одна пара до/после `@`, домен с точкой). Не RFC 5322 —
# отсекает очевидно невалидные адреса reply (ADR-044 §8).
_REPLY_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Потолки норм reply (ADR-044 §8).
MAX_REPLY_RECIPIENTS = 100  # суммарно to + cc
MAX_REPLY_SUBJECT_LEN = 998  # символов
MAX_REPLY_BODY_BYTES = 1024 * 1024  # 1 MiB непустого тела


class MailReplyError(Exception):
    """Нарушение норм reply (ADR-044 §8) — сервис маппит в 422 unprocessable."""


def validate_reply_addresses(addresses: list[str]) -> None:
    """Каждый адрес reply — валидный e-mail (ADR-044 §8). Иначе `MailReplyError`."""
    for addr in addresses:
        if _REPLY_EMAIL_RE.match(addr.strip()) is None:
            raise MailReplyError(f"Некорректный адрес получателя: {addr}")


# --- Производное имя ящика (ADR-047 §3.3) ------------------------------------

# Ведущая числовая часть (одно число или перечисление через запятую) + остаток текста
# (ADR-047 §3.1 — источник истины правила). DOTALL: остаток забирается целиком.
# ВНИМАНИЕ: копия этого правила живёт в миграции `0024` (Alembic-миграции не импортируют
# код приложения — ADR-047 §3.7 п.3). Обе реализации обязаны соответствовать §3.1 и
# покрываются одними нормативными тест-кейсами.
_LEADING_NUMBER_RE = re.compile(r"^\s*(\d+(?:\s*,\s*\d+)*)\s*(.*)$", re.DOTALL)


def parse_display_name(value: str | None) -> tuple[str | None, str | None]:
    """`display_name` → (`number`, `app_name`) по правилу разбора ADR-047 §3.1.

    Ведущая числовая часть (включая перечисление через запятую) → `number`
    (нормализуется к разделителю «запятая + пробел»); остаток текста → `app_name`
    (пустой → `None`). Нет ведущих цифр → `number = None`, `app_name` = `value.strip()`.
    `value is None`/пустой → обе части `None`.

    Единая чистая функция (ADR-047 §3.7 п.3): переиспользуется OAuth-ingest'ом
    (`MailAccountRepository.upsert_catalog`) и любым будущим путём импорта каталога.
    """
    if value is None:
        return None, None
    match = _LEADING_NUMBER_RE.match(value)
    if match is None:
        rest = value.strip()
        return None, rest or None
    number = ", ".join(token.strip() for token in match.group(1).split(","))
    app_name = match.group(2).strip()
    return number, app_name or None


def build_display_name(number: str | None, app_name: str | None) -> str | None:
    """`display_name` = `"<number> <app_name>"` (ADR-047 §3.3) — производное поле.

    Пустые/пробельные части опускаются; обе пусты → `None`. Пересчитывается сервером при
    каждом create/update ящика; клиент `display_name` не передаёт. Это единственная форма
    имени, уходящая во внешний контракт агрегатора (`number`/`app_name` наружу не идут).
    """
    parts = [part.strip() for part in (number, app_name) if part and part.strip()]
    return " ".join(parts) if parts else None


__all__ = [
    "MAX_REPLY_BODY_BYTES",
    "MAX_REPLY_RECIPIENTS",
    "MAX_REPLY_SUBJECT_LEN",
    "MailCursorError",
    "MailReplyError",
    "MailScope",
    "build_display_name",
    "decode_mail_cursor",
    "encode_mail_cursor",
    "parse_display_name",
    "validate_reply_addresses",
]
