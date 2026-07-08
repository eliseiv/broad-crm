"""Проверка доступности бэка `GET https://{domain}/health` (modules/backends, ADR-020).

Нормализация домена (принять с/без схемы, с завершающим `/` → «голый» `host[:port]`)
и валидация формата — чистые функции, тестируются без сети. Проверка: прямой
`GET https://{domain}/health` через `httpx.AsyncClient(verify=True,
follow_redirects=False)` с ограниченными ретраями. Строго `2xx` → `working`;
таймаут/сеть/не-2xx (после ретраев) → `error` с русской причиной. У бэков НЕТ
исхода `unknown`: недоступность бэка и есть событие. Путь `/health` и схема
`https://` фиксированы. Секретов нет; URL не секретен, но тела ответов не логируются.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import get_settings

CheckOutcome = Literal["working", "error"]

# Русскоязычные причины ошибки (записываются в error_message, modules/backends).
REASON_TIMEOUT = "Таймаут подключения"
REASON_UNREACHABLE = "Бэк недоступен"
REASON_BACKEND_ERROR = "Ошибка бэка"

# Фиксированы (не конфигурируются, ADR-020).
HEALTH_SCHEME = "https"
HEALTH_PATH = "/health"

# Backoff между попытками на транзиентных ошибках; попыток = len + 1 (т.е. 3).
_BACKOFF_DELAYS_SEC = (0.2, 0.5)

# DNS-метка: буквы/цифры/дефис, не начинается и не заканчивается дефисом.
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_HOST_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")


@dataclass(frozen=True)
class BackendCheckResult:
    """Чистый результат проверки: исход + причина (только при `error`)."""

    outcome: CheckOutcome
    reason: str | None


def normalize_domain(raw: str) -> str:
    """Нормализует домен → «голый» `host[:port]` (modules/backends, чистая функция).

    1. Trim пробелов. 2. Снять схему `http(s)://` (регистронезависимо). 3. Снять
    всё с первого `/` (путь/query/fragment). 4. Привести к нижнему регистру.
    """
    value = raw.strip()
    lowered = value.lower()
    if lowered.startswith("https://"):
        value = value[len("https://") :]
    elif lowered.startswith("http://"):
        value = value[len("http://") :]
    slash = value.find("/")
    if slash != -1:
        value = value[:slash]
    return value.lower()


def is_valid_domain(domain: str) -> bool:
    """Валидирует нормализованный домен: `host[:port]`, валидные DNS-метки, порт 1..65535.

    Ожидает уже нормализованный вход (без схемы/пути, нижний регистр). Пустой host,
    пробелы/`/`, невалидные метки или порт вне диапазона → False.
    """
    if not domain:
        return False
    host = domain
    if ":" in domain:
        host, _, port_str = domain.rpartition(":")
        if not port_str.isdigit():
            return False
        port = int(port_str)
        if not (1 <= port <= 65535):
            return False
    if not host:
        return False
    return _HOST_RE.fullmatch(host) is not None


def build_health_url(domain: str) -> str:
    """Собирает URL проверки здоровья: `https://{domain}/health` (схема/путь фиксированы)."""
    return f"{HEALTH_SCHEME}://{domain}{HEALTH_PATH}"


async def check_backend(domain: str) -> BackendCheckResult:
    """Проверяет доступность бэка через `GET https://{domain}/health`.

    Строго `2xx` → `working` (редиректы не следуются: `3xx` = ошибка здоровья).
    Таймаут → «Таймаут подключения»; сеть/DNS/TLS/транспорт → «Бэк недоступен»;
    не-2xx → «Ошибка бэка (HTTP N)»; прочая ошибка httpx → «Ошибка бэка». Исходы
    `error` заключаются только после ограниченных ретраев на транзиентных ошибках.
    """
    settings = get_settings()
    url = build_health_url(domain)
    max_attempts = len(_BACKOFF_DELAYS_SEC) + 1

    # Явный httpx.Timeout по ВСЕМ фазам (connect/read/write/pool), а не одиночный float —
    # чтобы connect/pool-фазы были ограничены явно (анти-зависание, ADR-024). Абсолютный
    # overall-deadline проверки — в мониторе (asyncio.wait_for), поверх этого таймаута.
    timeout = settings.backend_check_timeout_sec
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
        verify=True,
        follow_redirects=False,
    ) as client:
        for attempt in range(max_attempts):
            try:
                response = await client.get(url)
            except httpx.TimeoutException:
                # Таймаут — ретрай, затем конклюзивный error.
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return BackendCheckResult("error", REASON_TIMEOUT)
            except httpx.TransportError:
                # Сетевая/DNS/TLS/транспортная ошибка (ConnectError ⊂ TransportError) —
                # ретрай, затем «Бэк недоступен».
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return BackendCheckResult("error", REASON_UNREACHABLE)
            except httpx.HTTPError:
                # Прочая ошибка httpx — неретраябельна.
                return BackendCheckResult("error", REASON_BACKEND_ERROR)

            status_code = response.status_code
            if 200 <= status_code < 300:  # строго 2xx
                return BackendCheckResult("working", None)
            # Не-2xx (3xx/4xx/5xx) — конклюзивная «Ошибка бэка (HTTP N)».
            return BackendCheckResult("error", f"Ошибка бэка (HTTP {status_code})")

    # Недостижимо: цикл возвращает результат в каждой ветке.
    return BackendCheckResult("error", REASON_BACKEND_ERROR)


__all__ = [
    "HEALTH_PATH",
    "HEALTH_SCHEME",
    "REASON_BACKEND_ERROR",
    "REASON_TIMEOUT",
    "REASON_UNREACHABLE",
    "BackendCheckResult",
    "CheckOutcome",
    "build_health_url",
    "check_backend",
    "is_valid_domain",
    "normalize_domain",
]
