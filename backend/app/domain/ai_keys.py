"""Чистые доменные функции AI-ключей: фрагменты для маски и сборка `key_masked`.

Правило маски — modules/ai-keys#правило-маски-key_masked. Без сети/БД, тестируется
qa напрямую. Полный ключ здесь не хранится и не восстанавливается из фрагментов.
"""

from __future__ import annotations

# Разделитель маски — символ горизонтального многоточия U+2026.
_MASK_SEPARATOR = "…"
# Полная маска для короткого ключа (< 8 символов), где фрагменты пересеклись бы.
_FULL_MASK = "********"
# Минимальная длина, при которой сохраняются раздельные prefix/last4 (по 4 символа).
_MIN_FRAGMENT_LEN = 8


def compute_key_fragments(api_key: str) -> tuple[str | None, str | None]:
    """Вычисляет `(key_prefix, key_last4)` для маски.

    Ключ длиной >= 8 → первые 4 и последние 4 символа. Ключ короче 8 символов →
    `(None, None)` (фрагменты не сохраняются, будет полная маска). Вычисляется один
    раз при создании ключа (modules/ai-keys).
    """
    if len(api_key) >= _MIN_FRAGMENT_LEN:
        return api_key[:4], api_key[-4:]
    return None, None


def mask_key(key_prefix: str | None, key_last4: str | None) -> str:
    """Собирает `key_masked` из сохранённых фрагментов (modules/ai-keys).

    Оба фрагмента заданы → `"<prefix>…<last4>"`. Иначе (короткий ключ) → полная
    маска `"********"`. Полный ключ никогда не участвует.
    """
    if key_prefix is not None and key_last4 is not None:
        return f"{key_prefix}{_MASK_SEPARATOR}{key_last4}"
    return _FULL_MASK


__all__ = ["compute_key_fragments", "mask_key"]
