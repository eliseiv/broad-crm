"""In-memory rate-limit для /api/auth/login (05-security.md, TD-005).

На Этапе 1 — один воркер, счётчик в памяти. Распределённый вариант (Redis)
вынесен в TD-005.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class InMemoryRateLimiter:
    """Скользящее окно попыток по ключу (IP). Потокобезопасен."""

    def __init__(self, *, max_attempts: int, window_sec: int) -> None:
        self._max_attempts = max_attempts
        self._window_sec = window_sec
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> bool:
        """Регистрирует попытку и сообщает, не превышен ли лимит для ключа."""
        now = time.monotonic()
        cutoff = now - self._window_sec
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self._max_attempts:
                return False
            hits.append(now)
            return True


_limiter: InMemoryRateLimiter | None = None


def get_login_rate_limiter() -> InMemoryRateLimiter:
    """Синглтон лимитера логина с параметрами из настроек."""
    global _limiter
    if _limiter is None:
        from app.config import get_settings

        settings = get_settings()
        _limiter = InMemoryRateLimiter(
            max_attempts=settings.login_rate_limit_attempts,
            window_sec=settings.login_rate_limit_window_sec,
        )
    return _limiter
