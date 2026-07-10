"""Unit-тесты чистого домена почты `app/domain/mail.py` (ADR-044 §2/§7/§8).

Компаундный keyset-курсор `(internal_date, id)` (round-trip, opaque, устойчивость к
битому вводу — MINOR-2), нормы валидации reply-адресов (§8) и dataclass `MailScope`.
Без I/O — чистые функции.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from app.domain.mail import (
    MailCursorError,
    MailReplyError,
    MailScope,
    decode_mail_cursor,
    encode_mail_cursor,
    validate_reply_addresses,
)


# --- keyset-курсор -----------------------------------------------------------
def test_cursor_roundtrip_preserves_pair() -> None:
    d = datetime(2026, 7, 2, 9, 15, 30, tzinfo=UTC)
    token = encode_mail_cursor(d, 12345)
    decoded_d, decoded_id = decode_mail_cursor(token)
    assert decoded_d == d
    assert decoded_id == 12345


def test_cursor_is_opaque_base64_without_padding() -> None:
    token = encode_mail_cursor(datetime(2026, 1, 1, tzinfo=UTC), 1)
    assert "=" not in token  # padding снят
    assert "|" not in token  # разделитель не торчит наружу


def test_cursor_same_date_different_ids_distinct() -> None:
    d = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
    assert encode_mail_cursor(d, 100) != encode_mail_cursor(d, 101)


@pytest.mark.parametrize("bad", ["", "!!!not-base64!!!", "Zm9v", "b25seW9uZXBhcnQ"])
def test_cursor_invalid_raises(bad: str) -> None:
    with pytest.raises(MailCursorError):
        decode_mail_cursor(bad)


def test_cursor_naive_datetime_rejected() -> None:
    # Кодируем наивный datetime вручную и проверяем, что декодер требует таймзону.
    import base64

    raw = f"{datetime(2026, 1, 1).isoformat()}|5".encode()
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    with pytest.raises(MailCursorError):
        decode_mail_cursor(token)


# --- reply-адреса ------------------------------------------------------------
def test_valid_addresses_pass() -> None:
    validate_reply_addresses(["a@b.co", "  user.name@sub.example.org  "])


@pytest.mark.parametrize("bad", ["no-at", "a@b", "@b.co", "a@@b.co", "a b@c.co", ""])
def test_invalid_address_raises(bad: str) -> None:
    with pytest.raises(MailReplyError):
        validate_reply_addresses([bad])


def test_empty_list_is_noop() -> None:
    validate_reply_addresses([])  # пустой список — не ошибка (дефолт применяется выше)


# --- MailScope ---------------------------------------------------------------
def test_scope_is_frozen() -> None:
    scope = MailScope(sees_all_teams=False, team_ids=frozenset())
    with pytest.raises(FrozenInstanceError):
        scope.sees_all_teams = True  # type: ignore[misc]
