"""Клиент Telegram ИИ-бота базы знаний (modules/broadcast, ADR-076).

**Отдельный бот** (токен `KNOWLEDGE_BOT_TOKEN`) — рассылка сотрудникам в личку.
НЕ пересекается с notifier (`app/infra/telegram.py`), SMS-ботом и почтовыми ботами.
`sendMessage` **без** `parse_mode` (анти-инъекция разметки). TLS verify включён
(httpx, дефолт). Токен не логируется.

Типизированные ошибки: `TelegramForbiddenError` (403 / bot blocked / chat not
found — привязка мертва, `dead_at`); `TelegramApiError` — прочее (линк живой).
"""

from __future__ import annotations

from typing import Any

import httpx

_FORBIDDEN_MARKERS = (
    "bot was blocked",
    "chat not found",
    "user is deactivated",
    "bot can't initiate conversation",
    "peer_id_invalid",
    "bots can't send messages to bots",
)

_TIMEOUT_SEC = 30.0


class TelegramApiError(RuntimeError):
    """Ретраибельная ошибка Bot API (сеть/5xx/прочее)."""


class TelegramForbiddenError(TelegramApiError):
    """Не-ретраибельная ошибка: чат заблокирован/не найден (403/blocked)."""


class KnowledgeBotClient:
    """Обёртка над Bot API ИИ-бота базы знаний (токен `KNOWLEDGE_BOT_TOKEN`, ADR-076)."""

    def __init__(self, token: str) -> None:
        self.token = token.strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""

    @property
    def is_configured(self) -> bool:
        """Токен задан (`knowledge_bot_enabled`)."""
        return bool(self.token)

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_TIMEOUT_SEC, verify=True)

    async def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        """`sendMessage` без `parse_mode`. Успех → payload; иначе Forbidden/ApiError."""
        payload_json: dict[str, Any] = {"chat_id": chat_id, "text": text}
        try:
            async with self._build_client() as client:
                response = await client.post(f"{self.base_url}/sendMessage", json=payload_json)
        except httpx.HTTPError as exc:
            raise TelegramApiError(f"Telegram network error: {type(exc).__name__}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramApiError(
                f"Telegram sendMessage: invalid JSON, HTTP {response.status_code}"
            ) from exc

        if response.status_code < 400 and payload.get("ok"):
            return dict(payload)

        description = str(payload.get("description") or response.reason_phrase or "")
        lowered = description.lower()
        if response.status_code == 403 or any(marker in lowered for marker in _FORBIDDEN_MARKERS):
            raise TelegramForbiddenError(
                f"Telegram sendMessage forbidden: HTTP {response.status_code}: {description}"
            )
        raise TelegramApiError(
            f"Telegram sendMessage failed: HTTP {response.status_code}: {description}"
        )


__all__ = [
    "KnowledgeBotClient",
    "TelegramApiError",
    "TelegramForbiddenError",
]
