"""Unit-тесты доменных функций маски AI-ключа (modules/ai-keys#правило-маски-key_masked).

Побайтовая проверка `compute_key_fragments`/`mask_key`: ключ >= 8 → фрагменты
первые4/последние4 и маска `<pref>…<last4>` (разделитель U+2026); ключ < 8 →
фрагменты `(None, None)` и полная маска `********`. Без сети/БД.
"""

from __future__ import annotations

from app.domain.ai_keys import compute_key_fragments, mask_key

# Символ-разделитель маски — горизонтальное многоточие U+2026 (НЕ три точки ASCII).
SEPARATOR = "…"


def test_fragments_long_key_first4_last4() -> None:
    prefix, last4 = compute_key_fragments("sk-proj-ABCDEFGHbA3T")
    assert prefix == "sk-p"
    assert last4 == "bA3T"


def test_fragments_exactly_8_chars_boundary() -> None:
    # Длина ровно 8 → фрагменты сохраняются (границы не пересекаются).
    prefix, last4 = compute_key_fragments("ABCDefgh")
    assert prefix == "ABCD"
    assert last4 == "efgh"


def test_fragments_short_key_returns_none_none() -> None:
    # Длина 7 (< 8) → фрагменты не сохраняются.
    assert compute_key_fragments("ABCdefg") == (None, None)


def test_fragments_empty_key_returns_none_none() -> None:
    assert compute_key_fragments("") == (None, None)


def test_mask_long_key_uses_u2026_separator_byte_exact() -> None:
    masked = mask_key("sk-p", "bA3T")
    assert masked == "sk-p…bA3T"
    # Разделитель — ровно один символ U+2026, а не три ASCII-точки.
    assert SEPARATOR in masked
    assert "..." not in masked
    assert masked == f"sk-p{SEPARATOR}bA3T"


def test_mask_none_fragments_returns_full_mask() -> None:
    assert mask_key(None, None) == "********"


def test_mask_partial_none_returns_full_mask() -> None:
    # Любой отсутствующий фрагмент → полная маска (защитный кейс).
    assert mask_key("sk-p", None) == "********"
    assert mask_key(None, "bA3T") == "********"


def test_compute_then_mask_roundtrip_long() -> None:
    prefix, last4 = compute_key_fragments("openai-secret-key-XYZ9")
    assert mask_key(prefix, last4) == f"open{SEPARATOR}XYZ9"


def test_compute_then_mask_roundtrip_short_full_mask() -> None:
    prefix, last4 = compute_key_fragments("tiny")
    assert mask_key(prefix, last4) == "********"


def test_mask_never_contains_full_key() -> None:
    full = "sk-proj-SECRETMIDDLEbA3T"
    prefix, last4 = compute_key_fragments(full)
    masked = mask_key(prefix, last4)
    # Секрет (средняя часть) не восстанавливается из маски.
    assert "SECRETMIDDLE" not in masked
    assert full not in masked
