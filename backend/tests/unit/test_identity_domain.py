"""Тесты валидатора имени пользователя/роли (ADR-021, app/domain/identity.py).

Кириллица/юникод-буквы допускаются; обязательна хотя бы одна буква; длина 1–64 после
strip. Чистая функция; сервис преобразует IdentityNameError в 422 unprocessable.
"""

from __future__ import annotations

import pytest
from app.domain.identity import IdentityNameError, validate_identity_name


@pytest.mark.parametrize(
    "raw",
    [
        "Никита",
        "Иван-Петров",
        "user.01",
        "Админ",
        "admin",
        "Team Alpha",  # пробел внутри допустим
    ],
)
def test_valid_names_accepted(raw: str) -> None:
    assert validate_identity_name(raw) == raw


def test_name_is_stripped_before_validation() -> None:
    assert validate_identity_name("  Никита  ") == "Никита"


@pytest.mark.parametrize(
    "raw",
    [
        "123",  # только цифры — нет буквы
        "...",  # только пунктуация
        "   ",  # только пробелы → пусто после strip
        "",  # пусто
        "-_.",  # разрешённые символы, но без буквы
        "user@name",  # недопустимый символ @
        "a" * 65,  # длиннее 64
    ],
)
def test_invalid_names_rejected(raw: str) -> None:
    with pytest.raises(IdentityNameError):
        validate_identity_name(raw)


def test_max_length_boundary_after_strip() -> None:
    assert validate_identity_name("я" * 64) == "я" * 64
    with pytest.raises(IdentityNameError):
        validate_identity_name("я" * 65)
