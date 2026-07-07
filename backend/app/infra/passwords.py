"""Хэширование паролей БД-пользователей (bcrypt напрямую, ADR-021, 05-security.md).

В БД хранится ТОЛЬКО `password_hash`. Plaintext-пароль никогда не хранится, не
логируется и не возвращается в ответах API. Cost = дефолт bcrypt (12 раундов,
`bcrypt.gensalt()`). Пароль супер-админа (`.env`) bcrypt НЕ хэшируется — это
отдельная ветка (constant-time plaintext, auth_service).
"""

from __future__ import annotations

import bcrypt

# Известное ограничение bcrypt: значимы только первые 72 БАЙТА пароля
# (для кириллицы в UTF-8 — ~36 символов). Принято осознанно (05-security.md).
# bcrypt 4.x бросает ValueError на пароль > 72 байт, поэтому усекаем до 72 байт
# ОДИНАКОВО при хэшировании и проверке — консистентно (не дефект).
_MAX_BCRYPT_BYTES = 72


def _to_bcrypt_bytes(plain: str) -> bytes:
    """Кодирует пароль в UTF-8 и усекает до 72 байт (лимит bcrypt)."""
    return plain.encode("utf-8")[:_MAX_BCRYPT_BYTES]


def hash_password(plain: str) -> str:
    """Возвращает bcrypt-хэш пароля (ASCII-строка для хранения в `password_hash`)."""
    hashed = bcrypt.hashpw(_to_bcrypt_bytes(plain), bcrypt.gensalt())
    return hashed.decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Проверяет пароль против bcrypt-хэша. Некорректный хэш → False (без исключения)."""
    try:
        return bcrypt.checkpw(_to_bcrypt_bytes(plain), hashed.encode("ascii"))
    except ValueError:
        return False


__all__ = ["hash_password", "verify_password"]
