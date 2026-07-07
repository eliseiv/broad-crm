"""Валидация имени пользователя/роли (кириллица-допускающий формат, ADR-021).

Правило `username` — 03-data-model.md#правило-username-кириллица-допускающее-нормативно;
то же правило применяется к `roles.name` (04-api.md: «формат как username»). Чистая
функция без сети/БД; сервис преобразует `IdentityNameError` в 422 unprocessable.
"""

from __future__ import annotations

import re

# Юникод-буквы (любой алфавит, вкл. кириллицу), цифры, `_`, пробел, `.`, `-`;
# обязательна хотя бы одна буква (lookahead `[^\W\d_]`). Python `re` для `str` —
# юникодный по умолчанию: `Админ`, `Никита`, `user.01`, `Иван-Петров` валидны;
# `123`, `...`, `  ` — нет. Длина 1–64 (после strip) обеспечивается `{1,64}`.
_IDENTITY_NAME_RE = re.compile(r"^(?=.*[^\W\d_])[\w.\- ]{1,64}$")


class IdentityNameError(ValueError):
    """Имя пользователя/роли не соответствует формату (→ 422 unprocessable)."""


def validate_identity_name(raw: str) -> str:
    """Нормализует (`strip`) и валидирует имя; возвращает нормализованное значение.

    Нарушение формата → `IdentityNameError` (сервис → 422 unprocessable).
    """
    name = raw.strip()
    if _IDENTITY_NAME_RE.match(name) is None:
        raise IdentityNameError("Недопустимое имя")
    return name


__all__ = ["IdentityNameError", "validate_identity_name"]
