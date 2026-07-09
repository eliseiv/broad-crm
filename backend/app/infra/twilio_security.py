"""Валидация подписи Twilio-webhook (05-security.md#подпись-twilio, ADR-030).

Тонкая обёртка над `twilio.request_validator.RequestValidator`. URL для подписи
реконструируется вызывающим ТОЛЬКО из `SMS_PUBLIC_BASE_URL` + путь (единственный
источник истины; `X-Forwarded-*` не используется). `TWILIO_AUTH_TOKEN` — секрет,
не логируется.
"""

from __future__ import annotations

from twilio.request_validator import RequestValidator


def validate_twilio_signature(
    *,
    auth_token: str,
    signature: str | None,
    url: str,
    form_data: dict[str, str],
) -> bool:
    """True ⇔ `signature` валидна для `url` + `form_data` по `auth_token`.

    Отсутствующая подпись → False (→ 401 invalid_twilio_signature у вызывающего).
    """
    if not signature:
        return False
    validator = RequestValidator(auth_token)
    return bool(validator.validate(url, form_data, signature))
