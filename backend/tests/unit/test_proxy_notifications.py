"""Unit-тесты формата Telegram-сообщений прокси (modules/proxies#формат-сообщений).

Побайтовое соответствие двух шаблонов: 🔴 «Прокси не работает» и 🟢 «Прокси
восстановлен». Имя прокси — в двойных кавычках, идентификация — `<host>:<port>`
(порт обязателен). Текст plain (без parse_mode/Markdown). Без сети/БД.
"""

from __future__ import annotations

from app.domain.notifications import build_proxy_error, build_proxy_recovery


def test_proxy_error_message_byte_exact() -> None:
    text = build_proxy_error("DE Residential", "proxy.example.com", 1080, "Прокси недоступен")
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Прокси "DE Residential" proxy.example.com:1080\n'
        'Прокси не работает: "Прокси недоступен"'
    )


def test_proxy_error_header_exact() -> None:
    assert build_proxy_error("n", "h", 8080, "Ошибка прокси").startswith("🔴🔴🔴СРОЧНО🔴🔴🔴\n")


def test_proxy_error_reason_in_double_quotes() -> None:
    text = build_proxy_error("p", "1.2.3.4", 3128, "Таймаут подключения")
    assert 'Прокси не работает: "Таймаут подключения"' in text


def test_proxy_error_includes_host_and_port() -> None:
    # Порт обязателен в блоке идентификации `<host>:<port>`.
    text = build_proxy_error("p", "10.0.0.9", 65535, "Ошибка прокси")
    assert 'Прокси "p" 10.0.0.9:65535' in text


def test_proxy_recovery_message_byte_exact() -> None:
    text = build_proxy_recovery("DE Residential", "proxy.example.com", 1080)
    assert text == (
        "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n"
        'Прокси "DE Residential" proxy.example.com:1080\n'
        "Прокси снова работает"
    )


def test_proxy_recovery_header_exact() -> None:
    assert build_proxy_recovery("n", "h", 8080).startswith("🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n")


def test_proxy_name_in_double_quotes() -> None:
    assert 'Прокси "my-proxy"' in build_proxy_recovery("my-proxy", "h", 80)
    assert 'Прокси "my-proxy"' in build_proxy_error("my-proxy", "h", 80, "Ошибка прокси")


def test_all_three_reasons_render_verbatim() -> None:
    for reason in ("Таймаут подключения", "Прокси недоступен", "Ошибка прокси"):
        assert f'Прокси не работает: "{reason}"' in build_proxy_error("p", "h", 8080, reason)
