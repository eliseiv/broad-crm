"""Unit-тесты формата Telegram-сообщений AI-ключей (modules/ai-keys#формат-сообщений).

Побайтовое соответствие двух шаблонов: 🔴 «Ключ не работает» и 🟢 «Ключ восстановлен».
Имя ключа — в двойных кавычках, `<last4>` = `key_last4`; для короткого ключа
(`key_last4 = None`) подставляется пустая строка → `****`. Без сети/БД.
"""

from __future__ import annotations

from app.domain.notifications import build_key_error, build_key_recovery


def test_key_error_message_byte_exact() -> None:
    text = build_key_error("OpenAI Prod", "bA3T", "Недостаточно средств")
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Ключ "OpenAI Prod" ****bA3T\n'
        'Ключ не работает: "Недостаточно средств"'
    )


def test_key_error_header_exact() -> None:
    assert build_key_error("n", "1234", "Ключ недействителен").startswith("🔴🔴🔴СРОЧНО🔴🔴🔴\n")


def test_key_error_reason_in_double_quotes() -> None:
    text = build_key_error("k", "9999", "Доступ запрещён")
    assert 'Ключ не работает: "Доступ запрещён"' in text


def test_key_error_none_last4_becomes_empty_stars() -> None:
    # Короткий ключ (key_last4 = None) → маска ****  (без хвоста).
    text = build_key_error("Short", None, "Ошибка провайдера")
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n" 'Ключ "Short" ****\n' 'Ключ не работает: "Ошибка провайдера"'
    )
    assert "None" not in text


def test_key_recovery_message_byte_exact() -> None:
    text = build_key_recovery("OpenAI Prod", "bA3T")
    assert text == (
        "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n" 'Ключ "OpenAI Prod" ****bA3T\n' "Ключ снова работает"
    )


def test_key_recovery_header_exact() -> None:
    assert build_key_recovery("n", "1234").startswith("🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n")


def test_key_recovery_none_last4_becomes_empty_stars() -> None:
    text = build_key_recovery("Short", None)
    assert text == ("🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n" 'Ключ "Short" ****\n' "Ключ снова работает")
    assert "None" not in text


def test_key_name_in_double_quotes() -> None:
    assert 'Ключ "my-key"' in build_key_recovery("my-key", "0000")
    assert 'Ключ "my-key"' in build_key_error("my-key", "0000", "Ключ недействителен")


def test_all_four_reasons_render_verbatim() -> None:
    for reason in (
        "Ключ недействителен",
        "Доступ запрещён",
        "Недостаточно средств",
        "Ошибка провайдера",
    ):
        assert f'Ключ не работает: "{reason}"' in build_key_error("k", "abcd", reason)
