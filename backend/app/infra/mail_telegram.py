"""Клиент Telegram-ботов почты (ADR-044 §6/§9). Основной + 4 push-бота.

`MailBotClient` — обёртка над Bot API одного бота (свой токен): `sendMessage`
(`parse_mode=HTML`, опц. `reply_markup`), `answerCallbackQuery`, деплой-операции
`setWebhook`/`setMyCommands`. TLS verify включён (httpx-дефолт), токен не логируется.
Типизированные ошибки: `MailTelegramForbiddenError` (403/blocked/chat-not-found —
привязка мертва) vs ретраибельная `MailTelegramApiError` (сеть/5xx/429). Кнопка
«Посмотреть сообщение» несёт `callback_data=mail:{message_id}` (ADR-044 §6).
"""

from __future__ import annotations

from typing import Any

import httpx

# Подстроки в description Bot API, означающие «чат недоступен навсегда» (mark_dead).
_FORBIDDEN_MARKERS = (
    "bot was blocked",
    "chat not found",
    "user is deactivated",
    "bot can't initiate conversation",
    "peer_id_invalid",
    "bots can't send messages to bots",
)

_TIMEOUT_SEC = 30.0

_VIEW_MESSAGE_BUTTON = "Посмотреть сообщение"


class MailTelegramApiError(RuntimeError):
    """Ретраибельная ошибка Bot API почтового бота (сеть/5xx/429/прочее)."""


class MailTelegramForbiddenError(MailTelegramApiError):
    """Не-ретраибельная ошибка: чат заблокирован/не найден (403/blocked)."""


def view_message_markup(message_id: int) -> dict[str, Any]:
    """inline-кнопка «Посмотреть сообщение» с `callback_data=mail:{id}` (ADR-044 §6)."""
    return {
        "inline_keyboard": [[{"text": _VIEW_MESSAGE_BUTTON, "callback_data": f"mail:{message_id}"}]]
    }


def webapp_markup(webapp_url: str) -> dict[str, Any]:
    """inline-кнопка запуска Mini App (web_app) для приветствия `/start`."""
    return {"inline_keyboard": [[{"text": "Открыть почту", "web_app": {"url": webapp_url}}]]}


class MailBotClient:
    """Обёртка над Bot API одного почтового бота (свой токен, ADR-044 §9)."""

    def __init__(self, token: str, proxy_url: str = "") -> None:
        self.token = token.strip()
        self.proxy_url = proxy_url.strip()
        self.base_url = f"https://api.telegram.org/bot{self.token}" if self.token else ""

    @property
    def is_configured(self) -> bool:
        """Токен задан."""
        return bool(self.token)

    def _build_client(self) -> httpx.AsyncClient:
        if self.proxy_url:
            return httpx.AsyncClient(timeout=_TIMEOUT_SEC, proxy=self.proxy_url, verify=True)
        return httpx.AsyncClient(timeout=_TIMEOUT_SEC, verify=True)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """`sendMessage`. Успех → payload; иначе Forbidden/ApiError (типизированные)."""
        payload_json: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            payload_json["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload_json["reply_markup"] = reply_markup
        return await self._call_method("sendMessage", payload_json, classify_forbidden=True)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        """`answerCallbackQuery` — снять «часики» у пользователя. Ошибки проглатываются."""
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text is not None:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = True
        try:
            await self._call_method("answerCallbackQuery", payload)
        except MailTelegramApiError:
            # Снятие «часиков» best-effort — не критично для доставки.
            return

    async def set_webhook(self, *, url: str, secret_token: str) -> dict[str, Any]:
        """`setWebhook` с секрет-токеном (деплой-операция)."""
        return await self._call_method("setWebhook", {"url": url, "secret_token": secret_token})

    async def set_my_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        """`setMyCommands` — меню бота."""
        return await self._call_method("setMyCommands", {"commands": commands})

    async def _call_method(
        self, method: str, payload: dict[str, Any], *, classify_forbidden: bool = False
    ) -> dict[str, Any]:
        """Вызвать метод Bot API. Не логирует токен. classify_forbidden → 403→Forbidden."""
        try:
            async with self._build_client() as client:
                response = await client.post(f"{self.base_url}/{method}", json=payload)
        except httpx.HTTPError as exc:
            raise MailTelegramApiError(f"Telegram network error: {type(exc).__name__}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise MailTelegramApiError(
                f"Telegram {method}: invalid JSON, HTTP {response.status_code}"
            ) from exc

        if response.status_code < 400 and data.get("ok"):
            return dict(data)

        description = str(data.get("description") or response.reason_phrase or "")
        if classify_forbidden:
            lowered = description.lower()
            if response.status_code == 403 or any(m in lowered for m in _FORBIDDEN_MARKERS):
                raise MailTelegramForbiddenError(
                    f"Telegram {method} forbidden: HTTP {response.status_code}: {description}"
                )
        raise MailTelegramApiError(
            f"Telegram {method} failed: HTTP {response.status_code}: {description}"
        )


__all__ = [
    "MailBotClient",
    "MailTelegramApiError",
    "MailTelegramForbiddenError",
    "view_message_markup",
    "webapp_markup",
]
