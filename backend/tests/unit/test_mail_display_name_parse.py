"""Unit-тесты правила разбора «Номер»/«Приложение» и производного `display_name` (ADR-047 §3).

Источник истины правила — **ADR-047 §3.1**; реализаций у него ДВЕ (осознанно, §3.7 п.3):
1. чистая функция `app.domain.mail.parse_display_name` (её переиспользует OAuth-ingest —
   `MailAccountRepository.upsert_catalog`);
2. **копия в миграции** `0024_mail_accounts_number_app_name` (`_split_display_name`) —
   миграции не импортируют код приложения, иначе удаление модуля сломало бы миграцию
   задним числом.

Нормативные тест-кейсы §3.1 применяются к ОБЕИМ реализациям, плюс фиксируется их ПАРИТЕТ
(побайтовое совпадение результатов на общем наборе входов). Также покрыт `build_display_name`
(производное имя, §3.3).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Protocol

import pytest
from app.domain.mail import build_display_name, parse_display_name

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0024_mail_accounts_number_app_name.py"
)


class _Splitter(Protocol):
    def __call__(self, display_name: str) -> tuple[str | None, str | None]: ...


def _load_migration_splitter() -> _Splitter:
    """Загружает копию правила из тела миграции 0024 (файл вне пакета — грузим по пути)."""
    spec = importlib.util.spec_from_file_location("_mig_0024", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._split_display_name  # type: ignore[no-any-return]


_migration_split = _load_migration_splitter()

# Нормативные кейсы владельца (ADR-047 §3.1 — обязаны воспроизводиться побуквенно).
_NORMATIVE: list[tuple[str, str | None, str | None]] = [
    ("5108 Klyro Forge (Codex)", "5108", "Klyro Forge (Codex)"),
    ("173, 57, 104", "173, 57, 104", None),
    ("WIU", None, "WIU"),
]

# Дополнительные кейсы правила (regex `^\s*(\d+(?:\s*,\s*\d+)*)\s*(.*)$`, нормализация
# разделителя к «запятая + пробел», пустой остаток → None).
_EXTRA: list[tuple[str, str | None, str | None]] = [
    ("173,57 ,  104", "173, 57, 104", None),  # разделители нормализуются
    ("  5108   Klyro  ", "5108", "Klyro"),  # ведущие/хвостовые пробелы срезаются
    ("42", "42", None),  # только число → app_name пуст
    ("   ", None, None),  # пусто после strip → обе части None
    ("", None, None),
    ("v2 Beta", None, "v2 Beta"),  # цифра не ведущая → number = None
    ("5108, 42 Klyro", "5108, 42", "Klyro"),  # перечисление + остаток
    ("0007 App", "0007", "App"),  # ведущие нули не «нормализуются» в int
]


# ------------------------------ нормативные кейсы: чистая функция домена (§3.1)
@pytest.mark.parametrize(("display_name", "number", "app_name"), _NORMATIVE)
def test_domain_parse_normative_cases(
    display_name: str, number: str | None, app_name: str | None
) -> None:
    assert parse_display_name(display_name) == (number, app_name)


# --------------------------- нормативные кейсы: копия правила в миграции 0024 (§3.7 п.3)
@pytest.mark.parametrize(("display_name", "number", "app_name"), _NORMATIVE)
def test_migration_0024_split_normative_cases(
    display_name: str, number: str | None, app_name: str | None
) -> None:
    assert _migration_split(display_name) == (number, app_name)


# -------------------------------------------------- паритет двух реализаций (§3.7 п.3)
@pytest.mark.parametrize("display_name", [case[0] for case in _NORMATIVE + _EXTRA])
def test_domain_and_migration_implementations_are_in_parity(display_name: str) -> None:
    """Обе реализации одного правила обязаны соответствовать §3.1 — и, значит, друг другу."""
    assert parse_display_name(display_name) == _migration_split(display_name)


@pytest.mark.parametrize(("display_name", "number", "app_name"), _EXTRA)
def test_domain_parse_extra_cases(
    display_name: str, number: str | None, app_name: str | None
) -> None:
    assert parse_display_name(display_name) == (number, app_name)


def test_domain_parse_none_gives_both_none() -> None:
    """`display_name IS NULL` → обе колонки NULL (§3.1). У копии в миграции этой ветки нет:
    она вызывается только для строк `WHERE display_name IS NOT NULL`."""
    assert parse_display_name(None) == (None, None)


# ------------------------------------------- производный display_name (§3.3)
@pytest.mark.parametrize(
    ("number", "app_name", "expected"),
    [
        ("5108", "Klyro Forge (Codex)", "5108 Klyro Forge (Codex)"),
        ("173, 57, 104", None, "173, 57, 104"),
        (None, "WIU", "WIU"),
        (None, None, None),
        ("", "", None),  # пустые/пробельные части опускаются → обе пусты → None
        ("  ", "  ", None),
        ("5108", "", "5108"),
    ],
)
def test_build_display_name(number: str | None, app_name: str | None, expected: str | None) -> None:
    assert build_display_name(number, app_name) == expected


@pytest.mark.parametrize(("display_name", "_number", "_app_name"), _NORMATIVE)
def test_parse_then_build_roundtrip_is_canonical(
    display_name: str, _number: str | None, _app_name: str | None
) -> None:
    """`build_display_name(*parse_display_name(x))` — канон CRM; на нормативных входах = x."""
    assert build_display_name(*parse_display_name(display_name)) == display_name
