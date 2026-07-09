"""Проверка доступности бэка `GET {domain}health` (modules/backends, ADR-020/ADR-042).

Нормализация домена к канону `https://<host>/` (принять с/без схемы, с путём/
завершающим `/` → извлечь host → собрать `https://<host>/`) и валидация формата —
чистые функции, тестируются без сети. URL проверки строится **дописыванием** `health`
к канону (`domain + "health"`), а НЕ склейкой `https://{domain}/health` — иначе с
новым каноном вышел бы битый `https://https://…//health` (ADR-042, анти-двойная-схема).
Проверка: прямой `GET {domain}health` через `httpx.AsyncClient(verify=True,
follow_redirects=False)` с ограниченными ретраями. Строго `2xx` → `working`;
таймаут/сеть/не-2xx (после ретраев) → `error` с русской причиной. У бэков НЕТ
исхода `unknown`: недоступность бэка и есть событие. Путь `/health` и схема
`https://` фиксированы; секреты бэка в URL/лог не пишутся, тела ответов не логируются.
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

# Фиксированы (не конфигурируются, ADR-020/ADR-042). Канон домена — `https://<host>/`;
# суффикс здоровья дописывается к нему (`domain + "health"`), поэтому без ведущего `/`.
HEALTH_SCHEME = "https"
HEALTH_PATH = "/health"
_HEALTH_SUFFIX = "health"

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


def extract_host(raw: str) -> str:
    """Извлекает `host[:port]` из ввода (шаги 1–4 нормализации, чистая функция).

    1. Trim пробелов. 2. Снять схему `http(s)://` (регистронезависимо). 3. Снять
    всё с первого `/` (путь/query/fragment). 4. Привести к нижнему регистру.
    Валидацию формата host выполняет `is_valid_domain`; сборку канона — `normalize_domain`.
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


def normalize_domain(raw: str) -> str:
    """Нормализует домен → канон `https://<host>/` (ADR-042, чистая функция, не валидирует).

    Извлекает `host[:port]` (`extract_host`) и собирает канон `https://<host>/`.
    Примеры: `lumorixsite.shop` → `https://lumorixsite.shop/`;
    `HTTP://API.Example.com:8443/path?x=1` → `https://api.example.com:8443/`.
    """
    return f"{HEALTH_SCHEME}://{extract_host(raw)}/"


def is_valid_domain(domain: str) -> bool:
    """Валидирует host домена: валидные DNS-метки + опциональный `:port` (1..65535).

    Принимает как канон `https://<host>/`, так и голый `host[:port]` — host извлекается
    через `extract_host`. Пустой host, пробелы/`/` внутри host, невалидные метки или
    порт вне диапазона → False.
    """
    host_port = extract_host(domain)
    if not host_port:
        return False
    host = host_port
    if ":" in host_port:
        host, _, port_str = host_port.rpartition(":")
        if not port_str.isdigit():
            return False
        port = int(port_str)
        if not (1 <= port <= 65535):
            return False
    if not host:
        return False
    return _HOST_RE.fullmatch(host) is not None


def build_health_url(domain: str) -> str:
    """Собирает URL проверки здоровья из канона: `domain + "health"` (ADR-042).

    `domain` — канон `https://<host>/`, поэтому health-URL строится дописыванием
    `health` (а НЕ склейкой `https://{domain}/health`, дающей битую двойную схему):
    `https://<host>/` → `https://<host>/health`.
    """
    return f"{domain}{_HEALTH_SUFFIX}"


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
    "extract_host",
    "is_valid_domain",
    "normalize_domain",
]
