"""Unit-тесты keyset-курсора внешнего синка документов (app/domain/documents, ADR-060 §3)."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime

import pytest
from app.domain.documents import (
    DocumentCursorError,
    decode_document_cursor,
    encode_document_cursor,
)


def test_cursor_round_trip() -> None:
    ts = datetime(2026, 7, 18, 12, 30, 45, 123456, tzinfo=UTC)
    node_id = uuid.uuid4()
    token = encode_document_cursor(ts, node_id)
    decoded_ts, decoded_id = decode_document_cursor(token)
    assert decoded_ts == ts
    assert decoded_id == node_id


def test_cursor_is_opaque_base64url_without_padding() -> None:
    token = encode_document_cursor(datetime(2026, 1, 1, tzinfo=UTC), uuid.uuid4())
    assert "=" not in token  # padding снят


def test_empty_cursor_rejected() -> None:
    with pytest.raises(DocumentCursorError):
        decode_document_cursor("")


def test_garbage_cursor_rejected() -> None:
    with pytest.raises(DocumentCursorError):
        decode_document_cursor("!!!not-base64!!!")


def test_naive_datetime_cursor_rejected() -> None:
    """Курсор с наивным datetime (без tz) недопустим — позиция кодируется из TIMESTAMPTZ."""
    raw = f"{datetime(2026, 1, 1).isoformat()}|{uuid.uuid4()}".encode()
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    with pytest.raises(DocumentCursorError):
        decode_document_cursor(token)


def test_invalid_uuid_cursor_rejected() -> None:
    raw = f"{datetime(2026, 1, 1, tzinfo=UTC).isoformat()}|not-a-uuid".encode()
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    with pytest.raises(DocumentCursorError):
        decode_document_cursor(token)
