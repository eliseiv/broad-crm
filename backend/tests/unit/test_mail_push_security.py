"""Unit-тесты HMAC push-контракта агрегатор→CRM (ADR-044 §3, граница безопасности).

Проверяют `app.infra.mail_push_security`: каноническую подпись над **сырыми байтами**
тела, окно skew (в обе стороны), обработку битых/отсутствующих заголовков и —
критично — что подпись, посчитанная над ре-сериализованным JSON (`ensure_ascii=True`),
НЕ проходит против сырого тела с не-ASCII (кириллица/«»/эмодзи). Модуль импортирует
только `mail_push_security` (без FastAPI-приложения) — не зависит от прочего стека.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.infra.mail_push_security import (
    compute_mail_push_signature,
    verify_mail_push_signature,
)

_SECRET = "shared-push-secret-abc123"
_MAX_SKEW = 300
_NOW = 1_752_100_000


def _header(secret: str, timestamp: int, raw_body: bytes) -> str:
    return "sha256=" + compute_mail_push_signature(
        secret=secret, timestamp=timestamp, raw_body=raw_body
    )


# --------------------------------------------------------- каноническая форма подписи
def test_compute_matches_manual_byte_construction() -> None:
    """`mac_input = str(ts).encode('ascii') + b'.' + raw_body`, hex HMAC-SHA256."""
    ts = _NOW
    raw = b'{"messages":[]}'
    expected = hmac.new(
        _SECRET.encode("utf-8"),
        str(ts).encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    assert compute_mail_push_signature(secret=_SECRET, timestamp=ts, raw_body=raw) == expected


def test_valid_signature_within_skew_returns_true() -> None:
    raw = b'{"messages":[{"uid":1}]}'
    assert verify_mail_push_signature(
        secret=_SECRET,
        signature_header=_header(_SECRET, _NOW, raw),
        timestamp_header=str(_NOW),
        raw_body=raw,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


def test_signature_accepted_without_sha256_prefix() -> None:
    """Префикс `sha256=` опционален — голый hex тоже валиден."""
    raw = b"body"
    bare = compute_mail_push_signature(secret=_SECRET, timestamp=_NOW, raw_body=raw)
    assert verify_mail_push_signature(
        secret=_SECRET,
        signature_header=bare,
        timestamp_header=str(_NOW),
        raw_body=raw,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


# ------------------------------------------------------------- подмена тела → False
def test_tampered_body_rejected() -> None:
    signed = b'{"amount":100}'
    tampered = b'{"amount":999}'
    sig = _header(_SECRET, _NOW, signed)
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=sig,
        timestamp_header=str(_NOW),
        raw_body=tampered,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


def test_wrong_secret_rejected() -> None:
    raw = b"body"
    sig = _header("other-secret", _NOW, raw)
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=sig,
        timestamp_header=str(_NOW),
        raw_body=raw,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


# ------------------------------------------------------------- окно skew (обе стороны)
def test_skew_future_beyond_window_rejected() -> None:
    raw = b"body"
    ts = _NOW + _MAX_SKEW + 1
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=_header(_SECRET, ts, raw),
        timestamp_header=str(ts),
        raw_body=raw,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


def test_skew_past_beyond_window_rejected() -> None:
    raw = b"body"
    ts = _NOW - _MAX_SKEW - 1
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=_header(_SECRET, ts, raw),
        timestamp_header=str(ts),
        raw_body=raw,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


def test_skew_at_boundary_accepted() -> None:
    """`abs(now - ts) <= max_skew` — ровно на границе валиден (обе стороны)."""
    raw = b"body"
    for ts in (_NOW + _MAX_SKEW, _NOW - _MAX_SKEW):
        assert verify_mail_push_signature(
            secret=_SECRET,
            signature_header=_header(_SECRET, ts, raw),
            timestamp_header=str(ts),
            raw_body=raw,
            max_skew_sec=_MAX_SKEW,
            now=_NOW,
        )


# ------------------------------------------------------- битые/отсутствующие заголовки
def test_missing_signature_header_rejected() -> None:
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=None,
        timestamp_header=str(_NOW),
        raw_body=b"body",
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


def test_missing_timestamp_header_rejected() -> None:
    raw = b"body"
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=_header(_SECRET, _NOW, raw),
        timestamp_header=None,
        raw_body=raw,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


def test_non_numeric_timestamp_rejected() -> None:
    raw = b"body"
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=_header(_SECRET, _NOW, raw),
        timestamp_header="not-a-number",
        raw_body=raw,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )


# ----------------- КРИТИЧНО: подпись над сырыми байтами, не над ре-сериализацией ----
def test_signature_is_over_raw_bytes_not_reserialized_json() -> None:
    """Не-ASCII тело: подпись валидна над сырыми байтами (ensure_ascii=False), но
    подпись, посчитанная над `json.dumps(obj)` (default ensure_ascii=True → \\uXXXX),
    НЕ проходит против сырого тела. Ловит скрытую ре-сериализацию на приёмнике.
    """
    obj = {
        "subject": "Отчёт «июнь» 📊",
        "from_name": "Иван Петров",
        "body_text": "Здравствуйте — вложение прилагается ✉️",
    }
    raw_body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    ascii_reserialized = json.dumps(obj).encode("utf-8")  # ensure_ascii=True (default)
    assert raw_body != ascii_reserialized  # предпосылка теста

    # Подпись над сырыми байтами — валидна.
    sig_raw = _header(_SECRET, _NOW, raw_body)
    assert verify_mail_push_signature(
        secret=_SECRET,
        signature_header=sig_raw,
        timestamp_header=str(_NOW),
        raw_body=raw_body,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )

    # Подпись над ре-сериализованным (ensure_ascii=True) телом — НЕ проходит против
    # сырого тела (разные байты → разный HMAC).
    sig_reserialized = _header(_SECRET, _NOW, ascii_reserialized)
    assert not verify_mail_push_signature(
        secret=_SECRET,
        signature_header=sig_reserialized,
        timestamp_header=str(_NOW),
        raw_body=raw_body,
        max_skew_sec=_MAX_SKEW,
        now=_NOW,
    )
