"""Unit-тесты валидации/нормализации телеграм-ника (app.domain.telegram, ADR-025).

Формат (03-data-model.md): опциональный ведущий `@`, затем 5–32 символа [A-Za-z0-9_].
Хранится нормализованным: без `@`, lower-case. Чистые функции без сети/БД.
"""

from __future__ import annotations

import pytest
from app.domain.telegram import TelegramFormatError, normalize_telegram, validate_telegram


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("nikita", "nikita"),
        ("@Nikita", "nikita"),
        ("  @Nick_01  ", "nick_01"),
        ("USER_NAME", "user_name"),
        ("a" * 32, "a" * 32),  # верхняя граница длины
        ("abcde", "abcde"),  # нижняя граница (5)
    ],
)
def test_validate_telegram_normalizes(raw: str, expected: str) -> None:
    assert validate_telegram(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "abcd",  # 4 символа (< 5)
        "a" * 33,  # 33 символа (> 32)
        "bad nick",  # пробел
        "@@nikita",  # двойной @
        "ник_кириллица",  # не [A-Za-z0-9_]
        "with-dash",  # дефис не разрешён
        "",  # пусто
    ],
)
def test_validate_telegram_rejects_bad_format(raw: str) -> None:
    with pytest.raises(TelegramFormatError):
        validate_telegram(raw)


def test_normalize_telegram_strips_at_and_lowercases_without_validation() -> None:
    # normalize НЕ валидирует формат (используется для идентификатора входа).
    assert normalize_telegram("@MixedCase") == "mixedcase"
    assert normalize_telegram("  Spaced  ") == "spaced"
