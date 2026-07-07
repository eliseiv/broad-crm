"""Unit-тесты формата Telegram-сообщений бэков (modules/backends#формат-сообщений).

Побайтовое соответствие двух шаблонов: 🔴 «Бэк не работает» и 🟢 «Бэк восстановлен».
Блок идентификации — `Бэк "<name>" [<code>] <domain>` (имя в двойных кавычках, код в
квадратных скобках, домен как есть). Текст plain (без parse_mode/Markdown). Без сети/БД.
"""

from __future__ import annotations

from app.domain.notifications import build_backend_error, build_backend_recovery


def test_backend_error_message_byte_exact() -> None:
    text = build_backend_error("api-eu", "API EU", "api.example.com", "Бэк недоступен")
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Бэк "API EU" [api-eu] api.example.com\n'
        'Бэк не работает: "Бэк недоступен"'
    )


def test_backend_error_header_exact() -> None:
    assert build_backend_error("c", "n", "d", "Ошибка бэка").startswith("🔴🔴🔴СРОЧНО🔴🔴🔴\n")


def test_backend_error_reason_in_double_quotes() -> None:
    text = build_backend_error("c", "n", "d", "Таймаут подключения")
    assert 'Бэк не работает: "Таймаут подключения"' in text


def test_backend_error_block_format_name_code_domain() -> None:
    # Порядок и разметка блока: имя в кавычках, код в скобках, домен как есть.
    text = build_backend_error("api-eu", "API EU", "api.example.com:8443", "Ошибка бэка")
    assert 'Бэк "API EU" [api-eu] api.example.com:8443' in text


def test_backend_recovery_message_byte_exact() -> None:
    text = build_backend_recovery("api-eu", "API EU", "api.example.com")
    assert text == (
        "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n" 'Бэк "API EU" [api-eu] api.example.com\n' "Бэк снова работает"
    )


def test_backend_recovery_header_exact() -> None:
    assert build_backend_recovery("c", "n", "d").startswith("🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n")


def test_backend_name_in_double_quotes_both_builders() -> None:
    assert 'Бэк "my-backend"' in build_backend_recovery("c", "my-backend", "d")
    assert 'Бэк "my-backend"' in build_backend_error("c", "my-backend", "d", "Ошибка бэка")


def test_all_error_reasons_render_verbatim() -> None:
    for reason in (
        "Таймаут подключения",
        "Бэк недоступен",
        "Ошибка бэка (HTTP 500)",
        "Ошибка бэка",
    ):
        assert f'Бэк не работает: "{reason}"' in build_backend_error("c", "n", "d", reason)
