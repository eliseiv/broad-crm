"""HMAC-SHA256 верификация push-контракта агрегатор→CRM (ADR-044 §3, граница безопасности).

Каноническая форма подписи — **байтами, ИДЕНТИЧНО mail-агрегатор `ADR-0043` §2**
(f-string над `bytes` ЗАПРЕЩЁН — даёт `repr` `b'...'`, а не сами байты)::

    mac_input = str(timestamp).encode("ascii") + b"." + raw_body_bytes
    signature = hmac.new(secret.encode("utf-8"), mac_input, hashlib.sha256).hexdigest()

где `timestamp` — целое из `X-Mail-Timestamp` (десятичное ASCII), `raw_body_bytes` —
**сырое** тело запроса ДО JSON-парсинга (не re-serialized), разделитель — один байт
`b"."`. Сравнение — `hmac.compare_digest` (constant-time). Окно валидности —
`abs(now - timestamp) <= max_skew_sec` (не полный анти-replay; повтор гасится
идемпотентностью приёмника, §3). Секрет `MAIL_PUSH_SECRET` — только из env, не логируется.
"""

from __future__ import annotations

import hashlib
import hmac
import time

_SIGNATURE_PREFIX = "sha256="


def compute_mail_push_signature(*, secret: str, timestamp: int, raw_body: bytes) -> str:
    """Каноническая HMAC-SHA256-подпись над `str(timestamp) + "." + raw_body` (hex).

    Байтовое построение `mac_input` — БЕЗ f-string над `bytes`. Обе стороны (агрегатор
    и CRM) обязаны строить `mac_input` этим выражением побайтно.
    """
    mac_input = str(timestamp).encode("ascii") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), mac_input, hashlib.sha256).hexdigest()


def verify_mail_push_signature(
    *,
    secret: str,
    signature_header: str | None,
    timestamp_header: str | None,
    raw_body: bytes,
    max_skew_sec: int,
    now: int | None = None,
) -> bool:
    """True, если подпись валидна И timestamp в окне `±max_skew_sec`.

    Оба провала (протухший skew / неверная подпись / битые заголовки) → False (роутер
    отвечает 401 not_authenticated). Пустой секрет здесь НЕ обрабатывается — это 503
    на стороне роутера (проверяется до вызова).
    """
    if not signature_header or not timestamp_header:
        return False

    try:
        timestamp = int(timestamp_header.strip())
    except (ValueError, AttributeError):
        return False

    now_ts = int(time.time()) if now is None else now
    if abs(now_ts - timestamp) > max_skew_sec:
        return False

    provided = signature_header.strip()
    if provided.startswith(_SIGNATURE_PREFIX):
        provided = provided[len(_SIGNATURE_PREFIX) :]

    expected = compute_mail_push_signature(secret=secret, timestamp=timestamp, raw_body=raw_body)
    return hmac.compare_digest(provided, expected)
