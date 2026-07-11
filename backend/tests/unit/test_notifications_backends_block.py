"""Unit-тесты блока «Бэки:» в Telegram-алертах об ОШИБКАХ (ADR-046 §1, modules/notifier).

Побайтовое соответствие нормативным шаблонам
(modules/notifier «Блок "Бэки:" в алертах об ОШИБКАХ», modules/backends «Формат сообщений
Telegram» — источник истины формата строки бэка `Бэк "<name>" [<code>] <domain>`).

Покрывают:
- пустой перечень → блок НЕ добавляется вовсе (ни `Бэки:`, ни пустой строки): сообщение
  побайтово равно прежнему (регресс-эталон — сообщения БЕЗ аргумента `backends`);
- лимит `MAX_ALERT_BACKENDS = 10` и строка-остаток `… и ещё <N-10>` (`…` = U+2026);
- recovery-сообщения (`build_recovered`, `build_key_recovery`, `build_backend_recovery`,
  `build_proxy_recovery`) перечнем НЕ расширяются — у них и параметра такого нет;
- алерты бэков и прокси перечнем НЕ расширяются;
- порядок перечня — in-memory сортировка по КОРТЕЖУ `(position, code)` в Python
  (`to_backend_refs`), включая коды со СМЕШАННЫМ регистром: перенос тай-брейка в SQL
  `ORDER BY` запрещён нормой (коллация БД игнорирует регистр → порядок разошёлся бы).
"""

from __future__ import annotations

import inspect

from app.domain.notifications import (
    MAX_ALERT_BACKENDS,
    BackendRef,
    build_backend_error,
    build_backend_recovery,
    build_critical_load,
    build_key_error,
    build_key_recovery,
    build_offline,
    build_proxy_error,
    build_proxy_recovery,
    build_recovered,
    build_warning,
)
from app.services.alert_backend_refs import to_backend_refs

_B1: BackendRef = ("api-eu", "API EU", "https://eu.example.com")
_B2: BackendRef = ("web", "Web", "https://web.example.com")


class _Row:
    """Строка `backends` (position/code/name/domain) — вход `to_backend_refs`."""

    def __init__(self, position: int, code: str, name: str = "N", domain: str = "d") -> None:
        self.position = position
        self.code = code
        self.name = name
        self.domain = domain


def _refs(count: int) -> list[BackendRef]:
    return [(f"code-{i:02d}", f"Бэк {i}", f"https://b{i}.example.com") for i in range(count)]


# ---------------------------------------------------------------- пустой перечень
def test_warning_empty_backends_byte_equal_to_previous_message() -> None:
    """Пустой перечень → блок не добавляется: текст побайтово равен прежнему (без аргумента)."""
    with_arg = build_warning("web-01", "10.0.0.5", [("CPU", 83.0), ("RAM", 81.4)], ())
    assert with_arg == build_warning("web-01", "10.0.0.5", [("CPU", 83.0), ("RAM", 81.4)])
    assert with_arg == (
        "🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡\n"
        'Сервер "web-01"\n'
        "IP 10.0.0.5\n"
        "\n"
        "CPU: Нагрузка более 83%\n"
        "RAM: Нагрузка более 81%"
    )
    assert "Бэки:" not in with_arg


def test_critical_and_offline_empty_backends_byte_equal_to_previous_message() -> None:
    critical = build_critical_load("web-01", "10.0.0.5", [("CPU", 95.2)], ())
    offline = build_offline("web-01", "10.0.0.5", ())
    assert critical == build_critical_load("web-01", "10.0.0.5", [("CPU", 95.2)])
    assert offline == build_offline("web-01", "10.0.0.5")
    assert offline == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n" 'Сервер "web-01"\n' "IP 10.0.0.5\n" "\n" "Сервер не доступен"
    )
    assert "Бэки:" not in critical and "Бэки:" not in offline


def test_key_error_empty_backends_byte_equal_to_previous_message() -> None:
    text = build_key_error("OpenAI Prod", "bA3T", "Недостаточно средств", ())
    assert text == build_key_error("OpenAI Prod", "bA3T", "Недостаточно средств")
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Ключ "OpenAI Prod" ****bA3T\n'
        'Ключ не работает: "Недостаточно средств"'
    )
    assert "Бэки:" not in text


# ----------------------------------------------------------- блок с перечнем
def test_offline_with_backends_block_byte_exact() -> None:
    text = build_offline("web-01", "10.0.0.5", [_B1, _B2])
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Сервер "web-01"\n'
        "IP 10.0.0.5\n"
        "\n"
        "Сервер не доступен\n"
        "\n"
        "Бэки:\n"
        'Бэк "API EU" [api-eu] https://eu.example.com\n'
        'Бэк "Web" [web] https://web.example.com'
    )


def test_warning_with_backends_block_byte_exact() -> None:
    text = build_warning("web-01", "10.0.0.5", [("CPU", 83.0)], [_B1])
    assert text == (
        "🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡\n"
        'Сервер "web-01"\n'
        "IP 10.0.0.5\n"
        "\n"
        "CPU: Нагрузка более 83%\n"
        "\n"
        "Бэки:\n"
        'Бэк "API EU" [api-eu] https://eu.example.com'
    )


def test_critical_load_with_backends_block_byte_exact() -> None:
    text = build_critical_load("web-01", "10.0.0.5", [("CPU", 95.0)], [_B2])
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Сервер "web-01"\n'
        "IP 10.0.0.5\n"
        "\n"
        "CPU: Нагрузка более 95%\n"
        "\n"
        "Бэки:\n"
        'Бэк "Web" [web] https://web.example.com'
    )


def test_key_error_with_backends_block_byte_exact() -> None:
    text = build_key_error("OpenAI Prod", "bA3T", "Недостаточно средств", [_B1])
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n"
        'Ключ "OpenAI Prod" ****bA3T\n'
        'Ключ не работает: "Недостаточно средств"\n'
        "\n"
        "Бэки:\n"
        'Бэк "API EU" [api-eu] https://eu.example.com'
    )


# ------------------------------------------------------- лимит и строка-остаток
def test_max_alert_backends_is_ten() -> None:
    assert MAX_ALERT_BACKENDS == 10


def test_exactly_limit_backends_no_remainder_line() -> None:
    text = build_offline("s", "1.1.1.1", _refs(MAX_ALERT_BACKENDS))
    lines = text.split("\n")
    # Шапка (3 строки) + пустая + «Сервер не доступен» + пустая + «Бэки:» + 10 строк бэков.
    assert lines[6] == "Бэки:"
    assert len(lines) == 7 + MAX_ALERT_BACKENDS
    assert "… и ещё" not in text


def test_over_limit_prints_first_ten_and_remainder_line() -> None:
    total = 13
    text = build_offline("s", "1.1.1.1", _refs(total))
    lines = text.split("\n")
    backend_lines = lines[7:]
    assert len(backend_lines) == MAX_ALERT_BACKENDS + 1  # 10 бэков + строка-остаток
    # Первые 10 — в исходном порядке (порядок задаёт вызывающий).
    assert backend_lines[0] == 'Бэк "Бэк 0" [code-00] https://b0.example.com'
    assert backend_lines[9] == 'Бэк "Бэк 9" [code-09] https://b9.example.com'
    # Остаток объявляется числом; «…» — U+2026, единственный символ перед « и ещё».
    assert backend_lines[10] == "… и ещё 3"
    assert backend_lines[10][0] == "…"
    assert "code-10" not in text  # хвост в текст не попадает


def test_eleven_backends_remainder_is_one() -> None:
    text = build_offline("s", "1.1.1.1", _refs(MAX_ALERT_BACKENDS + 1))
    assert text.endswith("… и ещё 1")


# --------------------------------------- recovery/бэк/прокси перечнем НЕ расширяются
def test_recovery_builders_have_no_backends_parameter() -> None:
    """Recovery перечнем не расширяется (ADR-046 §1) — параметра `backends` у них НЕТ."""
    for builder in (
        build_recovered,
        build_key_recovery,
        build_backend_recovery,
        build_proxy_recovery,
    ):
        assert "backends" not in inspect.signature(builder).parameters


def test_server_recovery_message_has_no_backends_block() -> None:
    text = build_recovered("web-01", "10.0.0.5")
    assert "Бэки:" not in text
    assert text == (
        "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢\n" 'Сервер "web-01"\n' "IP 10.0.0.5\n" "\n" "Сервер снова в сети"
    )


def test_key_recovery_message_has_no_backends_block() -> None:
    assert "Бэки:" not in build_key_recovery("OpenAI Prod", "bA3T")


def test_backend_and_proxy_alerts_have_no_backends_parameter() -> None:
    """Алерты бэков и прокси перечнем НЕ расширяются (ADR-046 §1)."""
    for builder in (build_backend_error, build_proxy_error):
        assert "backends" not in inspect.signature(builder).parameters
    assert "Бэки:" not in build_proxy_error("p", "1.2.3.4", 8080, "timeout")
    # У алерта бэка «Бэк ...» — блок идентификации, а не перечень: заголовка «Бэки:» нет.
    assert "Бэки:" not in build_backend_error("web", "Web", "https://web/", "500")


# ------------------------------------- порядок перечня: (position, code) in-memory
def test_to_backend_refs_orders_by_position_then_code() -> None:
    refs = to_backend_refs(
        [
            _Row(1, "b-second", "B", "https://b/"),
            _Row(0, "z-first", "Z", "https://z/"),
            _Row(1, "a-second", "A", "https://a/"),
        ]
    )
    assert [code for code, _, _ in refs] == ["z-first", "a-second", "b-second"]


def test_to_backend_refs_tiebreak_is_codepoint_not_db_collation() -> None:
    """Тай-брейк по `code` — по КОДПОЙНТАМ Python (ADR-046 §1, перенос в SQL ORDER BY запрещён).

    Коллация БД (`en_US.UTF-8`) регистр игнорирует и дала бы «Api» < «apple» < «Zeta»;
    сортировка по кодпойнтам ставит ВСЕ заглавные раньше строчных: «Api» < «Zeta» < «apple».
    Именно кодпойнты обеспечивают побайтовую воспроизводимость текста алерта независимо от
    локали инстанса БД.
    """
    refs = to_backend_refs(
        [
            _Row(0, "apple"),
            _Row(0, "Zeta"),
            _Row(0, "Api"),
        ]
    )
    assert [code for code, _, _ in refs] == ["Api", "Zeta", "apple"]


def test_to_backend_refs_projects_code_name_domain_triples() -> None:
    refs = to_backend_refs([_Row(0, "api-eu", "API EU", "https://eu.example.com")])
    assert refs == [("api-eu", "API EU", "https://eu.example.com")]


def test_to_backend_refs_empty_input_is_empty_block() -> None:
    assert to_backend_refs([]) == []
    assert "Бэки:" not in build_offline("s", "1.1.1.1", to_backend_refs([]))
