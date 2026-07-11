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

# Ссылка на бэк для блока «Бэки:» в алертах об ошибках (ADR-046 §1): (code, name, domain).
BackendRef = tuple[str, str, str]
# Лимит строк перечня бэков в одном алерте (ADR-046 §1, TD-053): при N > 10 печатаются
# первые 10, остаток объявляется строкой «… и ещё <N-10>» (лимит Telegram — 4096 символов).
MAX_ALERT_BACKENDS = 10


def _server_block(name: str, ip: str) -> str:
    """Блок идентификации сервера: имя в кавычках + IP."""
    return f'Сервер "{name}"\nIP {ip}'


def _metric_lines(items: Sequence[MetricItem]) -> str:
    """Строки метрик: `<LABEL>: Нагрузка более <int(usage_percent)>%`."""
    return "\n".join(f"{label}: Нагрузка более {int(percent)}%" for label, percent in items)


def _backend_block(code: str, name: str, domain: str) -> str:
    """Блок идентификации бэка: имя в кавычках + код в скобках + домен как есть."""
    return f'Бэк "{name}" [{code}] {domain}'


def _backends_block(backends: Sequence[BackendRef]) -> str:
    """Блок «Бэки:» для алертов об ОШИБКАХ (ADR-046 §1, modules/notifier).

    Пустой перечень → `""` (блок не добавляется вовсе: ни заголовка, ни пустой строки —
    сообщение побайтово равно прежнему). Иначе — пустая строка, `Бэки:` и по строке на
    бэк (формат — переиспользуемый `_backend_block`, источник истины modules/backends).
    При `N > MAX_ALERT_BACKENDS` печатаются первые 10 (порядок задаёт вызывающий:
    `position ASC, code ASC`), последней строкой — `… и ещё <N-10>` (`…` = U+2026).
    """
    if not backends:
        return ""
    shown = backends[:MAX_ALERT_BACKENDS]
    lines = [_backend_block(code, name, domain) for code, name, domain in shown]
    rest = len(backends) - len(shown)
    if rest > 0:
        lines.append(f"… и ещё {rest}")
    return "\n\nБэки:\n" + "\n".join(lines)


def build_warning(
    name: str, ip: str, items: Sequence[MetricItem], backends: Sequence[BackendRef] = ()
) -> str:
    """🟡 ПРЕДУПРЕЖДЕНИЕ — метрики, эскалировавшие в жёлтую зону (+ перечень бэков)."""
    return (
        f"{_WARNING_HEADER}\n{_server_block(name, ip)}\n\n{_metric_lines(items)}"
        f"{_backends_block(backends)}"
    )


def build_critical_load(
    name: str, ip: str, items: Sequence[MetricItem], backends: Sequence[BackendRef] = ()
) -> str:
    """🔴 СРОЧНО (красная зона) — метрики, эскалировавшие в красную зону (+ перечень бэков)."""
    return (
        f"{_CRITICAL_HEADER}\n{_server_block(name, ip)}\n\n{_metric_lines(items)}"
        f"{_backends_block(backends)}"
    )


def build_offline(name: str, ip: str, backends: Sequence[BackendRef] = ()) -> str:
    """🔴 СРОЧНО (offline) — переход online→offline (+ перечень бэков сервера)."""
    return (
        f"{_CRITICAL_HEADER}\n{_server_block(name, ip)}\n\nСервер не доступен"
        f"{_backends_block(backends)}"
    )


def build_recovered(name: str, ip: str) -> str:
    """🟢 ВОССТАНОВЛЕНО (recovery) — переход offline→online (ADR-018)."""
    return f"{_RECOVERY_HEADER}\n{_server_block(name, ip)}\n\nСервер снова в сети"


def _key_block(name: str, last4: str | None) -> str:
    """Блок идентификации ключа: имя в кавычках + маска `****<last4>`.

    Для короткого ключа (`key_last4 = None`) подставляется пустая строка → `****`.
    """
    return f'Ключ "{name}" ****{last4 or ""}'


def build_key_error(
    name: str, last4: str | None, reason: str, backends: Sequence[BackendRef] = ()
) -> str:
    """🔴 Ключ не работает — переход `pending|working → error` (+ перечень бэков ключа)."""
    return (
        f'{_CRITICAL_HEADER}\n{_key_block(name, last4)}\nКлюч не работает: "{reason}"'
        f"{_backends_block(backends)}"
    )


def build_key_recovery(name: str, last4: str | None) -> str:
    """🟢 Ключ восстановлен — переход `error → working` (modules/ai-keys)."""
    return f"{_RECOVERY_HEADER}\n{_key_block(name, last4)}\nКлюч снова работает"


def _proxy_block(name: str, host: str, port: int) -> str:
    """Блок идентификации прокси: имя в кавычках + `<host>:<port>`."""
    return f'Прокси "{name}" {host}:{port}'


def build_proxy_error(name: str, host: str, port: int, reason: str) -> str:
    """🔴 Прокси не работает — переход `pending|working → error` (modules/proxies)."""
    return f'{_CRITICAL_HEADER}\n{_proxy_block(name, host, port)}\nПрокси не работает: "{reason}"'


def build_proxy_recovery(name: str, host: str, port: int) -> str:
    """🟢 Прокси восстановлен — переход `error → working` (modules/proxies)."""
    return f"{_RECOVERY_HEADER}\n{_proxy_block(name, host, port)}\nПрокси снова работает"


def build_backend_error(code: str, name: str, domain: str, reason: str) -> str:
    """🔴 Бэк не работает — переход `pending|working → error` (modules/backends)."""
    return f'{_CRITICAL_HEADER}\n{_backend_block(code, name, domain)}\nБэк не работает: "{reason}"'


def build_backend_recovery(code: str, name: str, domain: str) -> str:
    """🟢 Бэк восстановлен — переход `error → working` (modules/backends)."""
    return f"{_RECOVERY_HEADER}\n{_backend_block(code, name, domain)}\nБэк снова работает"


__all__ = [
    "MAX_ALERT_BACKENDS",
    "METRIC_LABELS",
    "BackendRef",
    "MetricItem",
    "build_backend_error",
    "build_backend_recovery",
    "build_critical_load",
    "build_key_error",
    "build_key_recovery",
    "build_offline",
    "build_proxy_error",
    "build_proxy_recovery",
    "build_recovered",
    "build_warning",
]
