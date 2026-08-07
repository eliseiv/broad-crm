"""Шифрование секретов at-rest (Fernet, ADR-007/ADR-010, 03-data-model.md).

Ключ FERNET_KEY (base64, 32 байта) берётся из настроек. Один примитив и один
ключ — на все секреты CRM, шифруемые at-rest (ADR-007): функции ниже безразличны
к классу секрета, и состав классов на них не влияет. Нормативный перечень классов
живёт в docs/05-security.md, раздел «Управление секретами»; здесь он намеренно
не дублируется, чтобы не заводить второй, расходящийся с ним источник.

Plaintext-секрет существует только в памяти — при создании записи и при
расшифровке непосредственно перед использованием; он не логируется и не покидает
процесс.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CryptoError(Exception):
    """Ошибка шифрования/расшифровки секрета."""


def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.fernet_key:
        raise CryptoError("FERNET_KEY не задан в окружении")
    try:
        return Fernet(settings.fernet_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise CryptoError("FERNET_KEY имеет неверный формат") from exc


def encrypt_secret(plaintext: str) -> bytes:
    """Шифрует произвольный секрет, возвращает ciphertext (bytea для БД)."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    """Расшифровывает секрет. Результат не логируется и не покидает процесс."""
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("Не удалось расшифровать секрет (неверный ключ/данные)") from exc


def encrypt_password(plaintext: str) -> bytes:
    """Шифрует SSH-пароль (тонкий алиас `encrypt_secret`)."""
    return encrypt_secret(plaintext)


def decrypt_password(ciphertext: bytes) -> str:
    """Расшифровывает SSH-пароль (тонкий алиас `decrypt_secret`)."""
    return decrypt_secret(ciphertext)
