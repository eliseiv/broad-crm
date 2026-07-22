r"""Разбор и проверка приватного SSH-ключа (ADR-067 §3 п.4, 04-api.md#post-apiservers).

Чистые правила без I/O: нормализация ввода, **структурное** определение `is_encrypted`,
загрузка `cryptography` и проверка типа ключа, а также пере-сериализация в незашифрованный
OpenSSH-PEM для провижининга (ADR-067 §5).

**Почему процедура именно такая (нормативно).** Наивное `load_ssh_private_key(data,
password=...)` требуемых исходов не даёт: на `cryptography` 43.x все негативные ветки
приходят одним `ValueError` (различить «битый ключ» / «нужна фраза» / «неверная фраза» по
типу исключения нельзя, а разбирать его ТЕКСТ запрещено — он не контракт библиотеки и может
нести фрагменты материала), а лишний пароль к незашифрованному ключу может быть молча
проигнорирован. Поэтому исход задаёт НАША проверка: `is_encrypted` определяется структурно
(шаг 1), сверяется с наличием парольной фразы (шаг 2), и только потом ключ грузится (шаг 3);
ветка отказа выбирается по уже известному `is_encrypted`. Тип ключа проверяется отдельно
(шаг 4) — DSA отвергается на валидации, иначе он упал бы уже на провижининге.

Текст исключения `cryptography` наружу НЕ пробрасывается — сообщения фиксированы ниже.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
    load_ssh_private_key,
)

# --- Поля ответа 422 (04-api.md#post-apiservers) ---
FIELD_PRIVATE_KEY: Final = "ssh_private_key"
FIELD_PASSPHRASE: Final = "ssh_key_passphrase"

# --- Фиксированные сообщения (текст исключения библиотеки не используется) ---
MSG_UNPARSABLE: Final = "Не удалось разобрать приватный SSH-ключ"
MSG_UNSUPPORTED_TYPE: Final = "Тип ключа не поддерживается"
MSG_PASSPHRASE_NOT_NEEDED: Final = "Ключ не защищён парольной фразой — уберите её"
MSG_PASSPHRASE_REQUIRED: Final = "Ключ защищён парольной фразой — укажите её"
MSG_PASSPHRASE_WRONG: Final = "Неверная парольная фраза"

_ARMOR_BEGIN: Final = "-----BEGIN "
_ARMOR_END: Final = "-----"
_LABEL_ENCRYPTED_PKCS8: Final = "ENCRYPTED PRIVATE KEY"
_LABEL_OPENSSH: Final = "OPENSSH PRIVATE KEY"
_LABEL_SUFFIX: Final = "PRIVATE KEY"
# Незашифрованные PKCS#8 / PKCS#1 / SEC1 — самый частый случай (`ssh-keygen -m PEM`,
# ключи облачных провайдеров, экспорт из `openssl`). `DSA PRIVATE KEY` здесь НЕ
# отвергается: тип проверяется шагом 4, чтобы сообщение было корректным.
_PLAIN_PEM_LABELS: Final = frozenset(
    {"PRIVATE KEY", "RSA PRIVATE KEY", "EC PRIVATE KEY", "DSA PRIVATE KEY"}
)
_LEGACY_ENCRYPTED_HEADERS: Final = ("Proc-Type: 4,ENCRYPTED", "DEK-Info:")
_OPENSSH_MAGIC: Final = b"openssh-key-v1\x00"
_OPENSSH_CIPHER_NONE: Final = b"none"

# ECDSA — только NIST P-256/384/521 (ADR-067 §3 п.4 шаг 4).
_ALLOWED_EC_CURVES: Final = frozenset({"secp256r1", "secp384r1", "secp521r1"})

# Исключения, которыми `cryptography` сообщает о неудачной загрузке ключа. Ловятся как
# ГРУППА, потому что ветка отказа выбирается по `is_encrypted`, а не по типу исключения.
_LOAD_ERRORS: Final = (ValueError, TypeError, UnsupportedAlgorithm)


class SshKeyError(Exception):
    """Ошибка разбора/проверки ключа: несёт поле контракта и фиксированное сообщение."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True, slots=True)
class KeyStructure:
    """Результат шага 1: зашифрован ли ключ и каким загрузчиком его читать."""

    is_encrypted: bool
    is_openssh: bool


def normalize_private_key(raw: str) -> str:
    """Нормализация перед проверкой и шифрованием (ADR-067 §3 п.3).

    `CRLF → LF`, срез хвостовых пробелов, гарантированный завершающий `\n` (OpenSSH
    отвергает ключ без newline в конце файла). Хранится нормализованная форма.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return f"{text}\n" if text else ""


def analyze_private_key(text: str) -> KeyStructure:
    """Шаг 1: структурное определение `is_encrypted` (без криптоопераций).

    Ветки проверяются СВЕРХУ ВНИЗ, первое совпадение выигрывает; последняя — catch-all,
    ни один ввод не проваливается мимо всех. Публичный ключ (`ssh-rsa AAAA…`), сертификат,
    строка `known_hosts`, произвольный текст и OPENSSH-armor с битой base64 → `SshKeyError`.
    """
    label = _armor_label(text)
    if label is None or not label.endswith(_LABEL_SUFFIX):
        raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE)
    if label == _LABEL_ENCRYPTED_PKCS8:
        return KeyStructure(is_encrypted=True, is_openssh=False)
    if any(header in text for header in _LEGACY_ENCRYPTED_HEADERS):
        # Legacy PEM с заголовком шифрования (`Proc-Type`/`DEK-Info`).
        return KeyStructure(is_encrypted=True, is_openssh=label == _LABEL_OPENSSH)
    if label in _PLAIN_PEM_LABELS:
        return KeyStructure(is_encrypted=False, is_openssh=False)
    if label == _LABEL_OPENSSH:
        return KeyStructure(is_encrypted=_openssh_is_encrypted(text), is_openssh=True)
    raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE)


def load_private_key(text: str, passphrase: str | None) -> PrivateKeyTypes:
    """Шаги 1–4: структура → кросс-проверка с фразой → загрузка → тип ключа.

    Возвращает загруженный объект ключа. Любое нарушение → `SshKeyError` с полем и
    фиксированным сообщением контракта.
    """
    structure = analyze_private_key(text)
    _check_passphrase_presence(structure, passphrase)
    key = _load(text, structure, passphrase)
    _check_key_type(key)
    return key


def validate_private_key(text: str, passphrase: str | None) -> None:
    """Проверка ключа при `POST /api/servers` (материал наружу не возвращается)."""
    load_private_key(text, passphrase)


def to_openssh_unencrypted(text: str, passphrase: str | None) -> str:
    """Пере-сериализация в НЕзашифрованный OpenSSH-PEM для провижининга (ADR-067 §5 п.2).

    Снимает парольную фразу **в памяти**: дальше она не идёт никуда — ни в файл, ни в env,
    ни в argv, ни в лог. Ключ, который не грузится, даёт `SshKeyError` (провижининг
    переводит сервер в `error` с `"SSH key unusable"`).
    """
    key = load_private_key(text, passphrase)
    try:
        material = key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.OpenSSH,
            encryption_algorithm=NoEncryption(),
        )
    except _LOAD_ERRORS as exc:
        raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE) from exc
    return material.decode("utf-8")


def _armor_label(text: str) -> str | None:
    """Метка PEM-armor первой строки `-----BEGIN <label>-----` (или None)."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(_ARMOR_BEGIN) and stripped.endswith(_ARMOR_END):
            return stripped[len(_ARMOR_BEGIN) : -len(_ARMOR_END)].strip()
    return None


def _openssh_is_encrypted(text: str) -> bool:
    """`ciphername` формата `openssh-key-v1`: `none` → не зашифрован, иначе зашифрован.

    Битая base64 / отсутствие магии / обрезанное поле → `SshKeyError` (catch-all шага 1):
    именно этот случай пропустила бы regex-проверка заголовков.
    """
    blob = _openssh_body(text)
    if not blob.startswith(_OPENSSH_MAGIC):
        raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE)
    rest = blob[len(_OPENSSH_MAGIC) :]
    if len(rest) < 4:
        raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE)
    length = int.from_bytes(rest[:4], "big")
    if len(rest) < 4 + length:
        raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE)
    return rest[4 : 4 + length] != _OPENSSH_CIPHER_NONE


def _openssh_body(text: str) -> bytes:
    """Base64-тело между строками armor (строки-разделители отбрасываются)."""
    body_lines = [
        line.strip()
        for line in text.split("\n")
        if line.strip() and not line.strip().startswith("-----")
    ]
    try:
        return base64.b64decode("".join(body_lines), validate=True)
    except (ValueError, TypeError) as exc:
        raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE) from exc


def _check_passphrase_presence(structure: KeyStructure, passphrase: str | None) -> None:
    """Шаг 2: кросс-проверка структуры с вводом пользователя ДО загрузки.

    Исходы по фразе задаёт именно этот шаг, а не поведение библиотеки: на `cryptography`
    43.x лишний пароль к незашифрованному ключу может быть молча проигнорирован.
    """
    if not structure.is_encrypted and passphrase is not None:
        raise SshKeyError(FIELD_PASSPHRASE, MSG_PASSPHRASE_NOT_NEEDED)
    if structure.is_encrypted and passphrase is None:
        raise SshKeyError(FIELD_PASSPHRASE, MSG_PASSPHRASE_REQUIRED)


def _load(text: str, structure: KeyStructure, passphrase: str | None) -> PrivateKeyTypes:
    """Шаг 3: загрузка соответствующим загрузчиком; ветка отказа — по `is_encrypted`."""
    data = text.encode("utf-8")
    password = passphrase.encode("utf-8") if passphrase is not None else None
    try:
        if structure.is_openssh:
            return load_ssh_private_key(data, password=password)
        return load_pem_private_key(data, password=password)
    except _LOAD_ERRORS as exc:
        # Текст/тип исключения НЕ анализируется и наружу не идёт (ADR-067 §3 п.4).
        if structure.is_encrypted:
            raise SshKeyError(FIELD_PASSPHRASE, MSG_PASSPHRASE_WRONG) from exc
        raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNPARSABLE) from exc


def _check_key_type(key: PrivateKeyTypes) -> None:
    """Шаг 4: RSA / ECDSA (P-256/384/521) / Ed25519. **DSA отвергается здесь же.**

    Без этой проверки DSA прошёл бы форму и упал уже на провижининге: он deprecated в
    `cryptography` 43 и отключён в OpenSSH ≥ 7.0, а пере-сериализация в OpenSSH-формат
    (ADR-067 §5) на DSA > 1024 бит падает.
    """
    if isinstance(key, rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey):
        return
    if isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name in _ALLOWED_EC_CURVES:
        return
    raise SshKeyError(FIELD_PRIVATE_KEY, MSG_UNSUPPORTED_TYPE)
