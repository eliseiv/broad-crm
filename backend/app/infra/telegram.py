"""Тонкий async-клиент Telegram Bot API (httpx) для нотификатора (modules/notifier).

Best-effort доставка с ограниченными ретраями на транзиентные ошибки;
семантика at-least-once (при ретрае возможен редкий дубликат). `send_message`
НЕ пробрасывает ошибки наружу: при исчерпании ретраев уведомление пропускается
с warning-логом, метод возвращает флаг успеха. Токен и chat_id — секреты, в логи
не попадают (05-security.md): URL с токеном и тело сообщения не логируются.
"""

from __future__ import annotations

import asyncio

import httpx

from app.logging import get_logger

logger = get_logger(__name__)

# Таймаут запроса к Telegram API (рекомендация modules/notifier — 10 с).
_TELEGRAM_TIMEOUT_SEC = 10.0
# Транзиентные статусы, при которых имеет смысл ограниченный ретрай.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# Задержки backoff между попытками; число попыток = len + 1 (т.е. 3).
_BACKOFF_DELAYS_SEC = (0.2, 0.5)


class TelegramClient:
    """Обёртка над Telegram `sendMessage`. Не бросает наружу — возвращает bool."""

    def __init__(
        self, token: str, chat_id: str, timeout_sec: float = _TELEGRAM_TIMEOUT_SEC
    ) -> None:
        self._chat_id = chat_id
        self._timeout = timeout_sec
        # URL содержит токен — НЕ логировать.
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"

    async def send_message(self, text: str) -> bool:
        """Отправляет plain-текст в чат. True при успехе, False при любой ошибке.

        Best-effort с ограниченными ретраями на 429/5xx/таймаут/сеть (по образцу
        PrometheusClient), семантика at-least-once: при ретрае после транзиентной
        ошибки возможен редкий дубликат. При исчерпании ретраев уведомление
        пропускается (метод возвращает False). Ошибки логируются как
        `notifier_telegram_send_failed` (warning) без секретов и тела сообщения;
        исключения наружу не пробрасываются.
        """
        body = {"chat_id": self._chat_id, "text": text}
        max_attempts = len(_BACKOFF_DELAYS_SEC) + 1
        async with httpx.AsyncClient(timeout=self._timeout, verify=True) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.post(self._url, json=body)
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code in _RETRYABLE_STATUS and attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "notifier_telegram_send_failed",
                        status=status_code,
                        attempt=attempt + 1,
                    )
                    return False
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                        continue
                    logger.warning(
                        "notifier_telegram_send_failed",
                        error_type=type(exc).__name__,
                        attempt=attempt + 1,
                    )
                    return False
                except httpx.HTTPError as exc:
                    logger.warning(
                        "notifier_telegram_send_failed",
                        error_type=type(exc).__name__,
                    )
                    return False
                else:
                    return True

        # Недостижимо: цикл либо возвращает, либо исчерпывает попытки выше.
        return False


__all__ = ["TelegramClient"]
