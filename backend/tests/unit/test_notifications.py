"""Unit-тесты формата Telegram-сообщений нотификатора (modules/notifier «Типы сообщений»).

Проверяется побайтовое соответствие трёх шаблонов, заголовки, блок идентификации
сервера, строки метрик, floor для int(usage_percent), стабильный порядок CPU/RAM/SSD.
"""

from __future__ import annotations

from app.domain.notifications import (
    METRIC_LABELS,
    build_critical_load,
    build_offline,
    build_warning,
)


def test_warning_message_byte_exact_multiple_metrics() -> None:
    text = build_warning("web-01", "10.0.0.5", [("CPU", 83.0), ("RAM", 81.4)])
    assert text == (
        "🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡\n"
        'Сервер "web-01"\n'
        "IP 10.0.0.5\n"
        "\n"
        "CPU: Нагрузка более 83%\n"
        "RAM: Нагрузка более 81%"
    )


def test_warning_message_single_metric() -> None:
    text = build_warning("db-02", "192.168.1.10", [("RAM", 80.0)])
    assert text == (
        "🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡\n"
        'Сервер "db-02"\n'
        "IP 192.168.1.10\n"
        "\n"
        "RAM: Нагрузка более 80%"
    )


def test_critical_load_message_byte_exact() -> None:
    text = build_critical_load("api-03", "10.1.2.3", [("CPU", 95.0)])
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n" 'Сервер "api-03"\n' "IP 10.1.2.3\n" "\n" "CPU: Нагрузка более 95%"
    )


def test_critical_load_multiple_metrics_order_cpu_ram_ssd() -> None:
    # Порядок строк — ровно в том порядке, в каком переданы items (вызывающий
    # формирует их CPU, RAM, SSD из _METRIC_KEYS).
    text = build_critical_load("srv", "10.0.0.1", [("CPU", 99.0), ("RAM", 92.0), ("SSD", 91.0)])
    lines = text.splitlines()
    assert lines[-3:] == [
        "CPU: Нагрузка более 99%",
        "RAM: Нагрузка более 92%",
        "SSD: Нагрузка более 91%",
    ]


def test_offline_message_byte_exact() -> None:
    text = build_offline("worker-09", "172.16.0.4")
    assert text == (
        "🔴🔴🔴СРОЧНО🔴🔴🔴\n" 'Сервер "worker-09"\n' "IP 172.16.0.4\n" "\n" "Сервер не доступен"
    )


def test_warning_header_exact() -> None:
    assert build_warning("n", "1.2.3.4", [("CPU", 80.0)]).startswith("🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡\n")


def test_critical_headers_exact() -> None:
    assert build_critical_load("n", "1.2.3.4", [("CPU", 91.0)]).startswith("🔴🔴🔴СРОЧНО🔴🔴🔴\n")
    assert build_offline("n", "1.2.3.4").startswith("🔴🔴🔴СРОЧНО🔴🔴🔴\n")


def test_usage_percent_floor_not_round() -> None:
    # 87.9 → 87 (округление ВНИЗ, не банковское/арифметическое округление).
    assert "CPU: Нагрузка более 87%" in build_warning("n", "1.2.3.4", [("CPU", 87.9)])
    assert "RAM: Нагрузка более 90%" in build_critical_load("n", "1.2.3.4", [("RAM", 90.99)])
    # Целое значение остаётся как есть.
    assert "SSD: Нагрузка более 81%" in build_warning("n", "1.2.3.4", [("SSD", 81.0)])


def test_name_in_double_quotes_and_ip_label() -> None:
    text = build_offline('My "Prod" Box', "8.8.8.8")
    assert 'Сервер "My "Prod" Box"' in text
    assert "IP 8.8.8.8" in text


def test_metric_labels_mapping_stable() -> None:
    assert METRIC_LABELS == {"cpu": "CPU", "ram": "RAM", "ssd": "SSD"}
