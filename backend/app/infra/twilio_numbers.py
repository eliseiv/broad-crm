"""Twilio REST-клиент входящих номеров аккаунта (modules/sms, ADR-030).

Официальный Twilio SDK **синхронный** — вызывать только из threadpool
(`asyncio.to_thread`), иначе блокирует event loop. `.list()` обходит все страницы
(пагинация). TLS включён (дефолт SDK). Секреты (SID/token) не логируются.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests  # transitive-зависимость twilio
from twilio.base.exceptions import TwilioException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

# Размер страницы Twilio REST (аккаунт ~сотни номеров).
_PAGE_SIZE = 100
# Таймаут HTTP-запроса к Twilio (сек) — отказоустойчивость.
_HTTP_TIMEOUT_SECONDS = 30


class TwilioNotConfiguredError(RuntimeError):
    """`TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` не заданы (→ 503 twilio_not_configured)."""


class TwilioNumbersApiError(RuntimeError):
    """Сбой Twilio API: сеть, 5xx, таймаут, аутентификация (→ 502 twilio_error)."""


@dataclass(frozen=True, slots=True)
class TwilioNumber:
    """Входящий номер Twilio-аккаунта (E.164 + friendly_name)."""

    phone_number: str
    friendly_name: str | None


class TwilioNumbersClient:
    """Тянет входящие номера Twilio-аккаунта (`IncomingPhoneNumbers`)."""

    def __init__(self, account_sid: str, auth_token: str) -> None:
        self._sid = (account_sid or "").strip()
        self._token = (auth_token or "").strip()

    @property
    def is_configured(self) -> bool:
        """Оба креда заданы (иначе `list_incoming_numbers` → TwilioNotConfiguredError)."""
        return bool(self._sid and self._token)

    def list_incoming_numbers(self) -> list[TwilioNumber]:
        """Синхронный вызов Twilio REST — **только из threadpool** (`asyncio.to_thread`).

        `.list(page_size=...)` внутри SDK обходит все страницы (пагинация). Сбой
        сети/аутентификации/5xx/таймаут → TwilioNumbersApiError.
        """
        if not self.is_configured:
            raise TwilioNotConfiguredError(
                "TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN не сконфигурированы"
            )
        http_client = TwilioHttpClient(timeout=_HTTP_TIMEOUT_SECONDS)
        client = Client(self._sid, self._token, http_client=http_client)
        try:
            records = client.incoming_phone_numbers.list(page_size=_PAGE_SIZE)
        except TwilioException as exc:
            raise TwilioNumbersApiError(f"Twilio API error: {type(exc).__name__}") from exc
        except requests.RequestException as exc:
            raise TwilioNumbersApiError(f"Twilio network error: {type(exc).__name__}") from exc

        numbers: list[TwilioNumber] = []
        for rec in records:
            phone = getattr(rec, "phone_number", None)
            if not phone:
                continue
            friendly = getattr(rec, "friendly_name", None)
            numbers.append(
                TwilioNumber(
                    phone_number=str(phone),
                    friendly_name=str(friendly) if friendly else None,
                )
            )
        return numbers
