"""Валидация опционального email пользователя (ADR-022, 04-api.md#users).

Формат валидируется на уровне приложения (Pydantic не использует EmailStr, чтобы не
тянуть зависимость email-validator и контролировать 422 vs 400 — по образцу username).
Хранится нормализованным: `strip()` + `lower()`. Чистая функция без сети/БД; сервис
преобразует `EmailFormatError` в 422 unprocessable (`details[].field="email"`).
"""

from __future__ import annotations

import re

# Прагматичный формат: локальная часть и домен без пробелов/`@`, домен с точкой.
# Не претендует на RFC 5322 — отсекает очевидно невалидные значения; уникальность
# и хранение — на БД (частичный уникальный индекс uq_users_email).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_EMAIL_MAX_LEN = 254


class EmailFormatError(ValueError):
    """Email не соответствует формату (→ 422 unprocessable)."""


def validate_email(raw: str) -> str:
    """Нормализует (`strip`+`lower`) и валидирует email; возвращает нормализованный.

    Нарушение формата/длины → `EmailFormatError` (сервис → 422 unprocessable).
    """
    email = raw.strip().lower()
    if not email or len(email) > _EMAIL_MAX_LEN or _EMAIL_RE.match(email) is None:
        raise EmailFormatError("Недопустимый email")
    return email


__all__ = ["EmailFormatError", "validate_email"]
