"""Unit-тесты чистых доменных функций модуля «СМС» (app/domain/sms.py, ADR-030).

Без I/O: `normalize_phone` (E.164), keyset-курсор (`encode_cursor`/`decode_cursor`
round-trip + битые входы → SmsCursorError) и `verify_init_data` (HMAC-SHA256 + TTL,
время инъектируется — без реального `time.time()`). initData/секреты в тестах не логируются.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest
from app.domain.sms import (
    SmsCursorError,
    ValidatedInitData,
    decode_cursor,
    encode_cursor,
    normalize_phone,
    verify_init_data,
)

_BOT_TOKEN = "123456:TEST-BOT-TOKEN"


# --- normalize_phone --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+13105551234", "+13105551234"),
        ("13105551234", "+13105551234"),
        ("0013105551234", "+13105551234"),  # международный префикс 00 → +
        ("+1 (310) 555-1234", "+13105551234"),  # мусорные символы вычищаются
        ("+7 916 123-45-67", "+79161234567"),
        ("", ""),  # пусто → пусто
        ("abc", ""),  # без цифр → пусто
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


# --- keyset-курсор ----------------------------------------------------------


def test_cursor_round_trip_preserves_position() -> None:
    received_at = datetime(2026, 7, 9, 12, 34, 56, tzinfo=UTC)
    token = encode_cursor(received_at, 1057)
    decoded_at, decoded_id = decode_cursor(token)
    assert decoded_at == received_at
    assert decoded_id == 1057


def test_cursor_is_opaque_base64url_without_padding() -> None:
    token = encode_cursor(datetime(2026, 1, 1, tzinfo=UTC), 1)
    assert "=" not in token  # padding срезан


def test_decode_empty_cursor_raises() -> None:
    with pytest.raises(SmsCursorError):
        decode_cursor("")


def test_decode_garbage_cursor_raises() -> None:
    with pytest.raises(SmsCursorError):
        decode_cursor("!!!not-base64!!!")


def test_decode_structurally_invalid_raises() -> None:
    token = base64.urlsafe_b64encode(b"notadatetime|5").rstrip(b"=").decode("ascii")
    with pytest.raises(SmsCursorError):
        decode_cursor(token)


def test_decode_naive_datetime_cursor_raises() -> None:
    # Позиция всегда кодируется из TIMESTAMPTZ; наивный datetime в курсоре запрещён.
    raw = f"{datetime(2020, 1, 1).isoformat()}|5".encode()
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    with pytest.raises(SmsCursorError):
        decode_cursor(token)


# --- verify_init_data (HMAC-SHA256 + TTL) -----------------------------------


def _build_init_data(
    *,
    bot_token: str = _BOT_TOKEN,
    user: dict[str, object] | None = None,
    auth_date: int,
    tamper_hash: bool = False,
    omit: str | None = None,
) -> str:
    """Собирает валидный (или намеренно битый) raw initData c корректным HMAC."""
    fields: dict[str, str] = {
        "query_id": "AAABBB",
        "auth_date": str(auth_date),
    }
    if omit != "user":
        fields["user"] = json.dumps(user if user is not None else {"id": 42, "first_name": "Оля"})
    if omit == "auth_date":
        fields.pop("auth_date", None)

    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    signature = hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if tamper_hash:
        signature = "deadbeef" + signature[8:]
    if omit != "hash":
        fields["hash"] = signature
    return urlencode(fields)


def test_verify_init_data_valid() -> None:
    now = int(datetime(2026, 7, 9, tzinfo=UTC).timestamp())
    raw = _build_init_data(auth_date=now)
    result = verify_init_data(raw, bot_token=_BOT_TOKEN, max_age_seconds=3600, now=now)
    assert isinstance(result, ValidatedInitData)
    assert result.telegram_user_id == 42
    assert result.first_name == "Оля"


def _verify(raw: str, *, bot_token: str = _BOT_TOKEN, max_age: int = 3600, now: int | None = None):
    """Короткая обёртка над verify_init_data (общие дефолты для тестов ошибок)."""
    return verify_init_data(raw, bot_token=bot_token, max_age_seconds=max_age, now=now)


def test_verify_init_data_hash_mismatch() -> None:
    now = 1_700_000_000
    raw = _build_init_data(auth_date=now, tamper_hash=True)
    assert _verify(raw, now=now) == "hash_mismatch"


def test_verify_init_data_wrong_bot_token_mismatch() -> None:
    now = 1_700_000_000
    raw = _build_init_data(auth_date=now)
    assert _verify(raw, bot_token="999:OTHER", now=now) == "hash_mismatch"


def test_verify_init_data_expired() -> None:
    auth_date = 1_700_000_000
    later = auth_date + 4000  # старше TTL
    raw = _build_init_data(auth_date=auth_date)
    assert _verify(raw, now=later) == "expired"


def test_verify_init_data_missing_hash() -> None:
    now = 1_700_000_000
    raw = _build_init_data(auth_date=now, omit="hash")
    assert _verify(raw, now=now) == "missing_hash"


def test_verify_init_data_missing_user() -> None:
    now = 1_700_000_000
    raw = _build_init_data(auth_date=now, omit="user")
    assert _verify(raw, now=now) == "missing_user"


def test_verify_init_data_empty_or_no_token_malformed() -> None:
    assert _verify("", now=1) == "malformed"
    assert _verify("user=x&hash=y", bot_token="", now=1) == "malformed"


def test_verify_init_data_invalid_user_payload() -> None:
    now = 1_700_000_000
    raw = _build_init_data(auth_date=now, user={"first_name": "БезID"})  # нет числового id
    assert (
        verify_init_data(raw, bot_token=_BOT_TOKEN, max_age_seconds=3600, now=now)
        == "invalid_user_payload"
    )


def test_verify_init_data_within_ttl_boundary() -> None:
    auth_date = 1_700_000_000
    at_limit = auth_date + 3600  # ровно на границе TTL → ещё валиден
    raw = _build_init_data(auth_date=auth_date)
    result = verify_init_data(raw, bot_token=_BOT_TOKEN, max_age_seconds=3600, now=at_limit)
    assert isinstance(result, ValidatedInitData)


def test_verify_init_data_default_now_uses_wallclock() -> None:
    # now=None → time.time(); свежий auth_date проходит без инъекции времени.
    auth_date = int((datetime.now(UTC) - timedelta(seconds=5)).timestamp())
    raw = _build_init_data(auth_date=auth_date)
    result = verify_init_data(raw, bot_token=_BOT_TOKEN, max_age_seconds=3600)
    assert isinstance(result, ValidatedInitData)
