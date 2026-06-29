"""Шифрование SSH-паролей (Fernet, ADR-007, 03-data-model.md).

Ключ FERNET_KEY (base64, 32 байта) берётся из настроек. Plaintext-пароль
существует только в памяти при создании сервера и при расшифровке перед Ansible.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CryptoError(Exception):
    """Ошибка шифрования/расшифровки SSH-пароля."""


def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.fernet_key:
        raise CryptoError("FERNET_KEY не задан в окружении")
    try:
        return Fernet(settings.fernet_key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise CryptoError("FERNET_KEY имеет неверный формат") from exc


def encrypt_password(plaintext: str) -> bytes:
    """Шифрует SSH-пароль, возвращает ciphertext (bytea для БД)."""
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_password(ciphertext: bytes) -> str:
    """Расшифровывает SSH-пароль. Результат не логируется и не покидает процесс."""
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("Не удалось расшифровать SSH-пароль (неверный ключ/данные)") from exc
