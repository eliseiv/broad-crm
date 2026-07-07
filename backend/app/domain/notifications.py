"""Построение текста Telegram-сообщений нотификатора (modules/notifier).

Чистые функции без сети/БД — формат сообщений побайтово соответствует
спецификации modules/notifier. `n%` = `int(usage_percent)` (округление ВНИЗ).
Порядок метрик стабильный: CPU, RAM, SSD. Текст plain (без parse_mode/Markdown).
"""

from __future__ import annotations

from collections.abc import Sequence

# Человекочитаемые метки метрик (порядок строк в сообщении — CPU, RAM, SSD).
METRIC_LABELS: dict[str, str] = {"cpu": "CPU", "ram": "RAM", "ssd": "SSD"}

_WARNING_HEADER = "🟡🟡🟡ПРЕДУПРЕЖДЕНИЕ🟡🟡🟡"
_CRITICAL_HEADER = "🔴🔴🔴СРОЧНО🔴🔴🔴"
_RECOVERY_HEADER = "🟢🟢🟢ВОССТАНОВЛЕНО🟢🟢🟢"

# Элемент сообщения о нагрузке: (метка метрики, usage_percent).
MetricItem = tuple[str, float]


def _server_block(name: str, ip: str) -> str:
    """Блок идентификации сервера: имя в кавычках + IP."""
    return f'Сервер "{name}"\nIP {ip}'


def _metric_lines(items: Sequence[MetricItem]) -> str:
    """Строки метрик: `<LABEL>: Нагрузка более <int(usage_percent)>%`."""
    return "\n".join(f"{label}: Нагрузка более {int(percent)}%" for label, percent in items)


def build_warning(name: str, ip: str, items: Sequence[MetricItem]) -> str:
    """🟡 ПРЕДУПРЕЖДЕНИЕ — метрики, эскалировавшие в жёлтую зону."""
    return f"{_WARNING_HEADER}\n{_server_block(name, ip)}\n\n{_metric_lines(items)}"


def build_critical_load(name: str, ip: str, items: Sequence[MetricItem]) -> str:
    """🔴 СРОЧНО (красная зона) — метрики, эскалировавшие в красную зону."""
    return f"{_CRITICAL_HEADER}\n{_server_block(name, ip)}\n\n{_metric_lines(items)}"


def build_offline(name: str, ip: str) -> str:
    """🔴 СРОЧНО (offline) — переход online→offline."""
    return f"{_CRITICAL_HEADER}\n{_server_block(name, ip)}\n\nСервер не доступен"


def build_recovered(name: str, ip: str) -> str:
    """🟢 ВОССТАНОВЛЕНО (recovery) — переход offline→online (ADR-018)."""
    return f"{_RECOVERY_HEADER}\n{_server_block(name, ip)}\n\nСервер снова в сети"


def _key_block(name: str, last4: str | None) -> str:
    """Блок идентификации ключа: имя в кавычках + маска `****<last4>`.

    Для короткого ключа (`key_last4 = None`) подставляется пустая строка → `****`.
    """
    return f'Ключ "{name}" ****{last4 or ""}'


def build_key_error(name: str, last4: str | None, reason: str) -> str:
    """🔴 Ключ не работает — переход `pending|working → error` (modules/ai-keys)."""
    return f'{_CRITICAL_HEADER}\n{_key_block(name, last4)}\nКлюч не работает: "{reason}"'


def build_key_recovery(name: str, last4: str | None) -> str:
    """🟢 Ключ восстановлен — переход `error → working` (modules/ai-keys)."""
    return f"{_RECOVERY_HEADER}\n{_key_block(name, last4)}\nКлюч снова работает"


__all__ = [
    "METRIC_LABELS",
    "MetricItem",
    "build_critical_load",
    "build_key_error",
    "build_key_recovery",
    "build_offline",
    "build_recovered",
    "build_warning",
]
