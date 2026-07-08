"""Проверка доступности прокси через эталонный URL (modules/proxies, ADR-019).

Собирает URL `scheme://[user:pass@]host:port` в памяти и выполняет `GET`
эталонного `PROXY_CHECK_URL` через `httpx.AsyncClient(proxy=...)`. Ответ 2xx/3xx →
`working`; таймаут/сеть/иное (после ограниченных ретраев) → `error` с русской
причиной. У прокси НЕТ исхода `unknown`: недоступность прокси и есть событие.
Пароль и собранный URL НИКОГДА не логируются. TLS verify включён.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

import httpx

from app.config import get_settings

CheckOutcome = Literal["working", "error"]

# Русскоязычные причины ошибки (записываются в error_message, modules/proxies).
REASON_TIMEOUT = "Таймаут подключения"
REASON_UNREACHABLE = "Прокси недоступен"
REASON_PROXY_ERROR = "Ошибка прокси"

# Backoff между попытками на транзиентных ошибках; попыток = len + 1 (т.е. 3).
_BACKOFF_DELAYS_SEC = (0.2, 0.5)


@dataclass(frozen=True)
class ProxyCheckResult:
    """Чистый результат проверки: исход + причина (только при `error`)."""

    outcome: CheckOutcome
    reason: str | None


def build_proxy_url(
    proxy_type: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
) -> str:
    """Собирает URL прокси `scheme://[user[:pass]@]host:port`.

    Логин/пароль включаются только если заданы (URL-энкодятся). Результат содержит
    пароль в открытом виде — НЕ логировать; используется только в памяти монитора.
    """
    userinfo = ""
    if username:
        userinfo = quote(username, safe="")
        if password:
            userinfo += ":" + quote(password, safe="")
        userinfo += "@"
    return f"{proxy_type}://{userinfo}{host}:{port}"


async def check_proxy(
    proxy_type: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
) -> ProxyCheckResult:
    """Проверяет доступность прокси через эталонный `GET PROXY_CHECK_URL`.

    2xx/3xx → `working`; таймаут → «Таймаут подключения»; сеть/прокси-соединение →
    «Прокси недоступен»; 4xx/5xx или прочая ошибка httpx → «Ошибка прокси». Все
    исходы `error` заключаются только после ограниченных ретраев на транзиентных
    ошибках. Пароль/URL не логируются.
    """
    settings = get_settings()
    url = build_proxy_url(proxy_type, host, port, username, password)
    max_attempts = len(_BACKOFF_DELAYS_SEC) + 1

    # Явный httpx.Timeout по ВСЕМ фазам (connect/read/write/pool), а не одиночный float —
    # особенно важно для socks5 (SOCKS-handshake может не соблюдать read-таймаут).
    # Абсолютный overall-deadline проверки — в мониторе (asyncio.wait_for), ADR-024.
    timeout = settings.proxy_check_timeout_sec
    async with httpx.AsyncClient(
        proxy=url,
        timeout=httpx.Timeout(connect=timeout, read=timeout, write=timeout, pool=timeout),
        verify=True,
    ) as client:
        for attempt in range(max_attempts):
            try:
                response = await client.get(settings.proxy_check_url)
            except httpx.TimeoutException:
                # Таймаут — ретрай, затем конклюзивный error (без логов с URL/паролем).
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return ProxyCheckResult("error", REASON_TIMEOUT)
            except httpx.TransportError:
                # Сетевая/транспортная ошибка, ошибка прокси-соединения — ретрай,
                # затем «Прокси недоступен» (ProxyError/ConnectError ⊂ TransportError).
                if attempt < max_attempts - 1:
                    await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                    continue
                return ProxyCheckResult("error", REASON_UNREACHABLE)
            except httpx.HTTPError:
                # Прочая ошибка httpx — неретраябельна.
                return ProxyCheckResult("error", REASON_PROXY_ERROR)

            status_code = response.status_code
            if 200 <= status_code < 400:  # 2xx/3xx (в т.ч. 204)
                return ProxyCheckResult("working", None)
            # 4xx/5xx от эталонного URL — конклюзивная «Ошибка прокси».
            return ProxyCheckResult("error", REASON_PROXY_ERROR)

    # Недостижимо: цикл возвращает результат в каждой ветке.
    return ProxyCheckResult("error", REASON_PROXY_ERROR)


__all__ = [
    "REASON_PROXY_ERROR",
    "REASON_TIMEOUT",
    "REASON_UNREACHABLE",
    "CheckOutcome",
    "ProxyCheckResult",
    "build_proxy_url",
    "check_proxy",
]
