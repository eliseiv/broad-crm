"""Валидация опционального телеграм-ника пользователя (ADR-025, 04-api.md#users).

Формат телеграм-ника (03-data-model.md#правило-telegram-телеграм-ник-нормативно):
опциональный ведущий `@`, затем 5–32 символа из `[A-Za-z0-9_]`. Хранится
нормализованным: ведущий `@` снят, lower-case (Telegram-ники регистронезависимы) →
канон `[a-z0-9_]{5,32}`. Чистая функция без сети/БД; сервис преобразует
`TelegramFormatError` в 422 unprocessable (`details[].field="telegram"`).
"""

from __future__ import annotations

import re

# Опциональный ведущий `@`, затем 5–32 символа [A-Za-z0-9_].
_TELEGRAM_RE = re.compile(r"^@?[A-Za-z0-9_]{5,32}$")


class TelegramFormatError(ValueError):
    """Телеграм-ник не соответствует формату (→ 422 unprocessable)."""


def normalize_telegram(raw: str) -> str:
    """Снимает ведущий `@` и приводит к нижнему регистру (канон для хранения/поиска).

    Не валидирует формат — используется как для сохранения (после `validate_telegram`),
    так и для нормализации идентификатора входа перед поиском по `telegram`.
    """
    value = raw.strip()
    if value.startswith("@"):
        value = value[1:]
    return value.lower()


def validate_telegram(raw: str) -> str:
    """Валидирует телеграм-ник и возвращает нормализованный канон `[a-z0-9_]{5,32}`.

    Нарушение формата → `TelegramFormatError` (сервис → 422 unprocessable).
    """
    value = raw.strip()
    if _TELEGRAM_RE.match(value) is None:
        raise TelegramFormatError("Недопустимый телеграм-ник")
    return normalize_telegram(value)


__all__ = ["TelegramFormatError", "normalize_telegram", "validate_telegram"]
