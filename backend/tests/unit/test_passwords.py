"""Тесты bcrypt-хэширования паролей БД-пользователей (ADR-021, app/infra/passwords.py).

Cost = дефолт bcrypt. Значимы первые 72 БАЙТА (кириллица в UTF-8 — ~36 символов) —
документированное ограничение, усечение консистентно при хэшировании и проверке.
"""

from __future__ import annotations

from app.infra.passwords import hash_password, verify_password


def test_hash_verify_roundtrip() -> None:
    hashed = hash_password("s3cret-pass")

    assert hashed != "s3cret-pass"  # хранится хэш, не plaintext
    assert hashed.startswith("$2")  # bcrypt-формат
    assert verify_password("s3cret-pass", hashed) is True
    assert verify_password("wrong-pass", hashed) is False


def test_hash_is_salted_unique_per_call() -> None:
    # Разные соли → разные хэши одного пароля, но оба верифицируются.
    a = hash_password("same-password")
    b = hash_password("same-password")

    assert a != b
    assert verify_password("same-password", a) is True
    assert verify_password("same-password", b) is True


def test_cyrillic_password_over_72_bytes_is_truncated_consistently() -> None:
    # Кириллица: 2 байта/символ UTF-8; 40 символов = 80 байт > 72.
    password = "П" * 40
    hashed = hash_password(password)

    assert verify_password(password, hashed) is True
    # Первые 36 символов (72 байта) значимы → совпадают с усечённым; отличие после 36-го
    # символа не влияет (документированное ограничение bcrypt).
    assert verify_password("П" * 36, hashed) is True
    # Отличие в пределах первых 72 байт → неверный пароль.
    assert verify_password("Пп" + "П" * 38, hashed) is False


def test_verify_with_malformed_hash_returns_false_without_raising() -> None:
    assert verify_password("whatever", "not-a-bcrypt-hash") is False
    assert verify_password("whatever", "") is False
