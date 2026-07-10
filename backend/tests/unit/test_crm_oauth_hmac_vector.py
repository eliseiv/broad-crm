"""Кросс-репо HMAC-вектор: подпись агрегатора == верификация CRM, байт-в-байт (ADR-045 §3).

Уведомление `POST /api/mail/oauth/ingest` подписывается тем же HMAC-контрактом, что и
`/api/mail/ingest` (ADR-044 §3): агрегатор считает `build_signature`, CRM верифицирует
`compute_mail_push_signature`. Обе стороны строят `mac_input` побайтно
(`str(ts).encode("ascii") + b"." + raw_body`) и обязаны получить ОДИН И ТОТ ЖЕ hex.

Здесь зафиксирован НЕИЗМЕНЯЕМЫЙ вектор (секрет + timestamp + сырое тело с не-ASCII
`display_name`): те же константы захардкожены в парном тесте агрегатора
(`tests/unit/test_crm_oauth_hmac_vector.py`). Если любой конец сменит канон подписи или
сериализацию тела — hex разойдётся и один из двух тестов упадёт (детект дрейфа контракта
в разных прогонах/репозиториях).
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.infra.mail_push_security import compute_mail_push_signature

# --- ЗАФИКСИРОВАННЫЙ КРОСС-РЕПО ВЕКТОР (идентичен стороне агрегатора) ---------
_VECTOR_SECRET = "shared-oauth-hmac-secret-v1"
_VECTOR_TS = 1_752_500_000
_VECTOR_BODY = {
    "crm_state": "Zm9vLmJhcg",
    "mail_account_id": 7,
    "email": "box@outlook.com",
    "display_name": "Иван Пётр 📧",  # не-ASCII: кириллица + эмодзи
    "is_active": True,
}
# Сырые байты тела: json.dumps(compact, ensure_ascii=False) — как сериализует агрегатор.
_VECTOR_RAW_HEX = (
    "7b2263726d5f7374617465223a225a6d39764c6d4a686367222c226d61696c5f6163636f"
    "756e745f6964223a372c22656d61696c223a22626f78406f75746c6f6f6b2e636f6d222c"
    "22646973706c61795f6e616d65223a22d098d0b2d0b0d0bd20d09fd191d182d18020f09f"
    "93a7222c2269735f616374697665223a747275657d"
)
_VECTOR_EXPECTED_SIG = "b0cfceb2e4ab9d0d49c8893a2a34b397a8e33f422758f7678016f1ce98d24ecc"


def _raw_body() -> bytes:
    return json.dumps(_VECTOR_BODY, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def test_vector_raw_bytes_are_stable() -> None:
    """Сериализация тела детерминирована и совпадает с зафиксированными байтами."""
    assert _raw_body().hex() == _VECTOR_RAW_HEX


def test_crm_verifier_matches_fixed_vector() -> None:
    """CRM `compute_mail_push_signature` над вектором == захардкоженный hex."""
    sig = compute_mail_push_signature(
        secret=_VECTOR_SECRET, timestamp=_VECTOR_TS, raw_body=_raw_body()
    )
    assert sig == _VECTOR_EXPECTED_SIG


def test_vector_hex_matches_manual_hmac() -> None:
    """Независимая перепроверка вектора «голым» hmac (без функции модуля)."""
    raw = bytes.fromhex(_VECTOR_RAW_HEX)
    expected = hmac.new(
        _VECTOR_SECRET.encode("utf-8"),
        str(_VECTOR_TS).encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    assert expected == _VECTOR_EXPECTED_SIG


def test_non_ascii_display_name_roundtrips_byte_for_byte() -> None:
    """Не-ASCII `display_name` не ре-сериализуется: подпись над сырыми байтами валидна,
    а над `ensure_ascii=True`-вариантом — НЕТ (разные байты → разный HMAC)."""
    raw = _raw_body()
    ascii_variant = json.dumps(_VECTOR_BODY, separators=(",", ":")).encode("utf-8")
    assert raw != ascii_variant
    assert compute_mail_push_signature(
        secret=_VECTOR_SECRET, timestamp=_VECTOR_TS, raw_body=raw
    ) != compute_mail_push_signature(
        secret=_VECTOR_SECRET, timestamp=_VECTOR_TS, raw_body=ascii_variant
    )
