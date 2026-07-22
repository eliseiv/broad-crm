"""Генерация приватных SSH-ключей ДЛЯ ТЕСТОВ в рантайме (ADR-067, 06-testing-strategy.md §43).

Приватные ключи (даже тестовые) в репозиторий **не коммитятся** — норма
[06-testing-strategy.md](../../docs/06-testing-strategy.md): «ключи для фикстур генерируются
в рантайме через `cryptography`». Здесь собраны все формы, которые обязана различать
4-шаговая процедура разбора (`app/domain/ssh_keys.py`):

- **форматы armor:** OpenSSH (`openssh-key-v1`), PKCS#8 (`PRIVATE KEY`), PKCS#1/SEC1
  (`RSA PRIVATE KEY` / `EC PRIVATE KEY` — вывод `ssh-keygen -m PEM`, самый частый ввод);
- **шифрование:** без фразы; PKCS#8 (`ENCRYPTED PRIVATE KEY`); legacy PEM (`Proc-Type:
  4,ENCRYPTED`); OpenSSH с ciphername ≠ `none`;
- **типы:** RSA, ECDSA (P-256/384/521 и заведомо запрещённая secp256k1), Ed25519, DSA.

Модуль без префикса `test_` — pytest его не коллектит.
"""

from __future__ import annotations

from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes

# RSA-2048 генерируется ~0.1 с, 4096 — до нескольких секунд. Кэшируем по параметрам, чтобы
# набор тестов не платил за генерацию повторно (ключи детерминированно не нужны — важны
# только структура и тип).
_RSA_CACHE: dict[int, rsa.RSAPrivateKey] = {}
_DSA_CACHE: dict[int, dsa.DSAPrivateKey] = {}

PASSPHRASE: Final = "correct horse battery staple"


def rsa_key(bits: int = 2048) -> rsa.RSAPrivateKey:
    if bits not in _RSA_CACHE:
        _RSA_CACHE[bits] = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return _RSA_CACHE[bits]


def ec_key(curve: ec.EllipticCurve) -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(curve)


def ed25519_key() -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.generate()


def dsa_key(bits: int = 2048) -> dsa.DSAPrivateKey:
    """DSA — заведомо ОТВЕРГАЕМЫЙ тип (шаг 4 процедуры, ADR-067 §3 п.4)."""
    if bits not in _DSA_CACHE:
        _DSA_CACHE[bits] = dsa.generate_private_key(key_size=bits)
    return _DSA_CACHE[bits]


def _encryption(passphrase: str | None) -> serialization.KeySerializationEncryption:
    if passphrase is None:
        return serialization.NoEncryption()
    return serialization.BestAvailableEncryption(passphrase.encode("utf-8"))


def to_openssh(key: PrivateKeyTypes, passphrase: str | None = None) -> str:
    """`-----BEGIN OPENSSH PRIVATE KEY-----` (формат `openssh-key-v1`, дефолт `ssh-keygen`)."""
    return key.private_bytes(  # type: ignore[union-attr]
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=_encryption(passphrase),
    ).decode("utf-8")


def to_pkcs8(key: PrivateKeyTypes, passphrase: str | None = None) -> str:
    """`PRIVATE KEY` без фразы / `ENCRYPTED PRIVATE KEY` с фразой (PKCS#8)."""
    return key.private_bytes(  # type: ignore[union-attr]
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=_encryption(passphrase),
    ).decode("utf-8")


def to_pkcs1(key: PrivateKeyTypes, passphrase: str | None = None) -> str:
    """Traditional OpenSSL PEM: `RSA PRIVATE KEY` / `EC PRIVATE KEY` / `DSA PRIVATE KEY`.

    Это вывод `ssh-keygen -m PEM` и `openssl genrsa` — по ADR-067 §3 п.4 **самый частый
    формат ввода**. Без фразы у него НЕТ заголовка `Proc-Type`, и реализация, где catch-all
    ловит его раньше PEM-ветки, отдала бы `422`.
    """
    return key.private_bytes(  # type: ignore[union-attr]
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=_encryption(passphrase),
    ).decode("utf-8")


def public_openssh(key: PrivateKeyTypes) -> str:
    """Публичный ключ `ssh-rsa AAAA…` — частая ошибка ввода (обязан давать 422)."""
    return (
        key.public_key()  # type: ignore[union-attr]
        .public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        )
        .decode("utf-8")
    )


def corrupt_base64_middle(pem: str) -> str:
    """Портит base64 В СЕРЕДИНЕ тела, оставляя строки armor нетронутыми.

    Гейт против «валидации regex по BEGIN/END»: заголовки целы, а тело не декодируется /
    не разбирается. Символ подменяется на заведомо не-base64 (`!`), чтобы отказ был
    гарантированным, а не зависел от удачи с padding'ом.
    """
    lines = pem.split("\n")
    body = [i for i, line in enumerate(lines) if line and not line.startswith("-----")]
    if not body:
        raise AssertionError("в PEM нет тела для порчи")
    target = body[len(body) // 2]
    line = lines[target]
    mid = len(line) // 2
    lines[target] = f"{line[:mid]}!{line[mid + 1 :]}"
    return "\n".join(lines)
