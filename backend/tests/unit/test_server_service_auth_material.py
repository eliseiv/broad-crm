"""Unit: правило «ровно один способ входа» и сборка материала (ADR-067 §3, 04-api.md).

`ServerService._build_auth_material` — единственное место, где решается, какой материал
уедет в БД. Прецеденция нормативна и проверяется **поэлементно**:

1. ровно один способ — лишнее поле «чужого» режима (даже `null`/`""`) и отсутствующее
   обязательное поле дают `422` с именем **именно этого** поля;
2. лимиты размера — **ДО** разбора (анти-DoS: многомегабайтная строка не уходит в
   `cryptography`);
3. нормализация ключа (CRLF→LF + завершающий `\\n`) — **хранится нормализованная форма**;
4. разбор `cryptography` (4 шага) — сообщение контракта, без текста исключения;
5. шифрование Fernet — plaintext ни в объекте материала, ни в логах.

Ключевой нюанс — **`model_fields_set`**: «поле не передано» и «передано как `null`»
различаются. Явный `ssh_password: null` при `auth_method='key'` — ошибка ввода (клиент
собрал тело обоих режимов), и контракт требует на неё `422`, а не молчаливого приёма.
"""

from __future__ import annotations

import pytest
import structlog
from app.errors import AppError
from app.models.server import ServerAuthMethod
from app.schemas.server import ServerCreateRequest
from app.services.server_service import ServerService
from cryptography.hazmat.primitives.asymmetric import ec
from ssh_key_helpers import (
    PASSPHRASE,
    corrupt_base64_middle,
    dsa_key,
    ec_key,
    ed25519_key,
    public_openssh,
    rsa_key,
    to_openssh,
    to_pkcs1,
    to_pkcs8,
)


def build(**overrides: object) -> ServerCreateRequest:
    """Тело запроса; `model_fields_set` несёт РОВНО переданные ключи (это и проверяется)."""
    payload: dict[str, object] = {"name": "Server 01", "ip": "10.0.0.10", "ssh_user": "root"}
    payload.update(overrides)
    return ServerCreateRequest(**payload)  # type: ignore[arg-type]


def material(**overrides: object) -> object:
    return ServerService._build_auth_material(build(**overrides))


def expect_422(**overrides: object) -> AppError:
    with pytest.raises(AppError) as exc:
        ServerService._build_auth_material(build(**overrides))
    assert exc.value.status_code == 422
    assert exc.value.code == "validation_error"
    return exc.value


def field_of(error: AppError) -> str:
    assert error.details, "422 обязан нести details[] с точным полем (04-api.md)"
    return str(error.details[0]["field"])


# --- Обратная совместимость: прежнее тело без auth_method ---------------------------


def test_legacy_body_without_auth_method_is_password_mode() -> None:
    """Регресс-гейт: `{name, ip, ssh_user, ssh_password}` → `auth_method='password'`."""
    result = material(ssh_password="secret")
    assert result.auth_method is ServerAuthMethod.password  # type: ignore[attr-defined]
    assert result.ssh_password_encrypted is not None  # type: ignore[attr-defined]
    assert result.ssh_private_key_encrypted is None  # type: ignore[attr-defined]
    assert result.ssh_key_passphrase_encrypted is None  # type: ignore[attr-defined]


def test_explicit_password_auth_method_is_equivalent() -> None:
    result = material(auth_method="password", ssh_password="secret")
    assert result.auth_method is ServerAuthMethod.password  # type: ignore[attr-defined]


# --- Правило «ровно один способ»: лишнее поле чужого режима -------------------------


def test_password_mode_rejects_private_key_field() -> None:
    """Оба материала сразу → `422` с именем **лишнего** поля."""
    error = expect_422(ssh_password="secret", ssh_private_key=to_openssh(rsa_key()))
    assert field_of(error) == "ssh_private_key"


def test_password_mode_rejects_passphrase_field() -> None:
    error = expect_422(ssh_password="secret", ssh_key_passphrase=PASSPHRASE)
    assert field_of(error) == "ssh_key_passphrase"


def test_password_mode_rejects_explicit_null_private_key() -> None:
    """`ssh_private_key: null` — ПЕРЕДАННОЕ поле чужого режима ⇒ `422`, а не «не задано».

    Различие делает `model_fields_set`: клиент, собравший тело обоих режимов и обнуливший
    неиспользуемое поле, совершил ошибку ввода, и она обязана быть видимой.
    """
    error = expect_422(ssh_password="secret", ssh_private_key=None)
    assert field_of(error) == "ssh_private_key"


def test_key_mode_rejects_password_field() -> None:
    error = expect_422(auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_password="p")
    assert field_of(error) == "ssh_password"


def test_key_mode_rejects_explicit_null_password() -> None:
    error = expect_422(auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_password=None)
    assert field_of(error) == "ssh_password"


def test_key_mode_rejects_empty_string_password() -> None:
    """`ssh_password: ''` при `auth_method='key'` — тоже переданное чужое поле."""
    error = expect_422(auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_password="")
    assert field_of(error) == "ssh_password"


# --- Правило «ровно один способ»: отсутствующий обязательный материал ---------------


def test_password_mode_without_any_material_names_missing_password() -> None:
    """Ни одного материала → `422` с именем **недостающего** поля."""
    error = expect_422()
    assert field_of(error) == "ssh_password"


def test_password_mode_empty_password_is_422() -> None:
    error = expect_422(ssh_password="")
    assert field_of(error) == "ssh_password"


def test_key_mode_without_private_key_is_422() -> None:
    error = expect_422(auth_method="key")
    assert field_of(error) == "ssh_private_key"


def test_key_mode_blank_private_key_is_422() -> None:
    """Строка из пробелов — не ключ (иначе она ушла бы в разбор и дала другое сообщение)."""
    error = expect_422(auth_method="key", ssh_private_key="   \n\t ")
    assert field_of(error) == "ssh_private_key"


# --- Пустая парольная фраза (нормативная семантика реализации) ----------------------


def test_key_mode_empty_passphrase_is_422_on_passphrase_field() -> None:
    """`ssh_key_passphrase: ''` → `422 field=ssh_key_passphrase`, а не «фраза не задана».

    Симметрично `ssh_password: ''`. Иначе фраза длиной 0 нарушала бы объявленный
    диапазон 1–256 незаметно для клиента и гасила бы исход «ключ не защищён фразой».
    """
    error = expect_422(
        auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_key_passphrase=""
    )
    assert field_of(error) == "ssh_key_passphrase"


def test_key_mode_whitespace_only_passphrase_is_422() -> None:
    """`'   '` — тот же исход: пробельная фраза не считается заданной по ошибке."""
    error = expect_422(
        auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_key_passphrase="   "
    )
    assert field_of(error) == "ssh_key_passphrase"


def test_key_mode_explicit_null_passphrase_means_not_provided() -> None:
    """`ssh_key_passphrase: null` = «не задана» ⇒ незашифрованный ключ проходит."""
    result = material(
        auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_key_passphrase=None
    )
    assert result.auth_method is ServerAuthMethod.key  # type: ignore[attr-defined]
    assert result.ssh_key_passphrase_encrypted is None  # type: ignore[attr-defined]


# --- Лимиты размера ------------------------------------------------------------------


def test_private_key_over_ssh_key_max_bytes_is_422_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Превышение `SSH_KEY_MAX_BYTES` → `422` **до** разбора (анти-DoS).

    Что проверка именно ДО разбора, видно по вводу: строка заведомо не является ключом,
    и разбор дал бы «не удалось разобрать», а не сообщение о длине.
    """
    from app.config import get_settings

    monkeypatch.setenv("SSH_KEY_MAX_BYTES", "512")
    get_settings.cache_clear()
    try:
        error = expect_422(auth_method="key", ssh_private_key="x" * 600)
        assert field_of(error) == "ssh_private_key"
        assert "длиннее" in error.message
    finally:
        get_settings.cache_clear()


def test_private_key_limit_is_measured_in_bytes_not_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Лимит **байтовый**: не-ASCII комментарий в ключе занимает 2 байта на символ."""
    from app.config import get_settings

    monkeypatch.setenv("SSH_KEY_MAX_BYTES", "100")
    get_settings.cache_clear()
    try:
        # 60 кириллических символов = 120 байт > 100, хотя len() == 60 < 100.
        error = expect_422(auth_method="key", ssh_private_key="я" * 60)
        assert field_of(error) == "ssh_private_key"
        assert "длиннее" in error.message
    finally:
        get_settings.cache_clear()


def test_password_over_256_chars_is_422() -> None:
    error = expect_422(ssh_password="p" * 257)
    assert field_of(error) == "ssh_password"


def test_passphrase_over_256_chars_is_422() -> None:
    error = expect_422(
        auth_method="key",
        ssh_private_key=to_openssh(rsa_key(), PASSPHRASE),
        ssh_key_passphrase="p" * 257,
    )
    assert field_of(error) == "ssh_key_passphrase"


# --- Разбор ключа: исходы контракта --------------------------------------------------


def test_valid_unencrypted_key_produces_key_material() -> None:
    result = material(auth_method="key", ssh_private_key=to_openssh(rsa_key()))
    assert result.auth_method is ServerAuthMethod.key  # type: ignore[attr-defined]
    assert result.ssh_private_key_encrypted  # type: ignore[attr-defined]
    assert result.ssh_password_encrypted is None  # type: ignore[attr-defined]
    assert result.ssh_key_passphrase_encrypted is None  # type: ignore[attr-defined]


def test_pem_armor_without_proc_type_is_accepted() -> None:
    """PKCS#1 без `Proc-Type` (`ssh-keygen -m PEM`) → принят (регресс-гейт шага 1)."""
    result = material(auth_method="key", ssh_private_key=to_pkcs1(rsa_key()))
    assert result.auth_method is ServerAuthMethod.key  # type: ignore[attr-defined]


def test_pkcs8_plain_armor_is_accepted() -> None:
    result = material(auth_method="key", ssh_private_key=to_pkcs8(rsa_key()))
    assert result.auth_method is ServerAuthMethod.key  # type: ignore[attr-defined]


def test_encrypted_key_with_correct_passphrase_is_accepted() -> None:
    result = material(
        auth_method="key",
        ssh_private_key=to_openssh(rsa_key(), PASSPHRASE),
        ssh_key_passphrase=PASSPHRASE,
    )
    assert result.ssh_key_passphrase_encrypted is not None  # type: ignore[attr-defined]


def test_encrypted_key_with_wrong_passphrase_is_422_on_passphrase() -> None:
    error = expect_422(
        auth_method="key",
        ssh_private_key=to_openssh(rsa_key(), PASSPHRASE),
        ssh_key_passphrase="не та фраза",
    )
    assert field_of(error) == "ssh_key_passphrase"
    assert error.message == "Неверная парольная фраза"


def test_encrypted_key_without_passphrase_is_422_on_passphrase() -> None:
    error = expect_422(auth_method="key", ssh_private_key=to_openssh(rsa_key(), PASSPHRASE))
    assert field_of(error) == "ssh_key_passphrase"


def test_passphrase_for_unencrypted_key_is_422_on_passphrase() -> None:
    error = expect_422(
        auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_key_passphrase=PASSPHRASE
    )
    assert field_of(error) == "ssh_key_passphrase"


def test_public_key_pasted_instead_of_private_is_422() -> None:
    error = expect_422(auth_method="key", ssh_private_key=public_openssh(rsa_key()))
    assert field_of(error) == "ssh_private_key"


def test_garbage_instead_of_key_is_422() -> None:
    error = expect_422(auth_method="key", ssh_private_key="это точно не ключ")
    assert field_of(error) == "ssh_private_key"


def test_corrupted_base64_middle_is_422() -> None:
    error = expect_422(
        auth_method="key", ssh_private_key=corrupt_base64_middle(to_openssh(rsa_key()))
    )
    assert field_of(error) == "ssh_private_key"


def test_dsa_key_is_422_unsupported_type() -> None:
    error = expect_422(auth_method="key", ssh_private_key=to_pkcs8(dsa_key(2048)))
    assert field_of(error) == "ssh_private_key"
    assert error.message == "Тип ключа не поддерживается"


def test_ec_curve_outside_whitelist_is_422() -> None:
    error = expect_422(auth_method="key", ssh_private_key=to_pkcs8(ec_key(ec.SECP256K1())))
    assert field_of(error) == "ssh_private_key"
    assert error.message == "Тип ключа не поддерживается"


@pytest.mark.parametrize(
    "curve", [ec.SECP256R1(), ec.SECP384R1(), ec.SECP521R1()], ids=["P-256", "P-384", "P-521"]
)
def test_ec_whitelisted_curves_accepted(curve: ec.EllipticCurve) -> None:
    result = material(auth_method="key", ssh_private_key=to_openssh(ec_key(curve)))
    assert result.auth_method is ServerAuthMethod.key  # type: ignore[attr-defined]


def test_ed25519_accepted() -> None:
    result = material(auth_method="key", ssh_private_key=to_openssh(ed25519_key()))
    assert result.auth_method is ServerAuthMethod.key  # type: ignore[attr-defined]


# --- Нормализация хранимой формы ------------------------------------------------------


def test_stored_key_is_normalized_crlf_to_lf_with_trailing_newline() -> None:
    """Ключ с `\\r\\n` и без завершающего `\\n` → в БД уходит нормализованная форма."""
    from app.infra.crypto import decrypt_secret

    pem = to_openssh(rsa_key())
    crlf = pem.replace("\n", "\r\n").rstrip()
    result = material(auth_method="key", ssh_private_key=crlf)

    stored = decrypt_secret(result.ssh_private_key_encrypted)  # type: ignore[attr-defined]
    assert "\r" not in stored
    assert stored.endswith("\n")
    assert stored == pem


def test_passphrase_stored_verbatim_not_normalized() -> None:
    """Фраза шифруется КАК ЕСТЬ: нормализация к ней неприменима (это не PEM)."""
    from app.infra.crypto import decrypt_secret

    phrase = "фраза с пробелом на конце "
    result = material(
        auth_method="key",
        ssh_private_key=to_openssh(rsa_key(), phrase),
        ssh_key_passphrase=phrase,
    )
    assert decrypt_secret(result.ssh_key_passphrase_encrypted) == phrase  # type: ignore[attr-defined]


# --- Секреты не утекают ---------------------------------------------------------------


def test_encrypted_material_is_not_plaintext() -> None:
    """Ciphertext ≠ plaintext: сырой ключ/фраза не лежат в байтах материала."""
    pem = to_openssh(rsa_key(), PASSPHRASE)
    result = material(auth_method="key", ssh_private_key=pem, ssh_key_passphrase=PASSPHRASE)

    blob = result.ssh_private_key_encrypted + result.ssh_key_passphrase_encrypted  # type: ignore[attr-defined]
    assert pem.encode("utf-8") not in blob
    assert PASSPHRASE.encode("utf-8") not in blob


def test_422_message_and_details_carry_no_key_material() -> None:
    """Ни сообщение, ни `details[]` не несут фрагментов ключа/фразы."""
    pem = to_openssh(rsa_key(), PASSPHRASE)
    body_line = next(line for line in pem.split("\n") if line and not line.startswith("-----"))
    error = expect_422(auth_method="key", ssh_private_key=pem, ssh_key_passphrase="не та фраза")

    rendered = f"{error.message} {error.details}"
    assert body_line not in rendered
    assert "не та фраза" not in rendered


def test_no_key_material_in_structlog_on_failure() -> None:
    """Ветка `422` ничего не логирует с материалом (текст исключения тоже не идёт в лог)."""
    pem = to_openssh(rsa_key(), PASSPHRASE)
    body_line = next(line for line in pem.split("\n") if line and not line.startswith("-----"))

    with structlog.testing.capture_logs() as logs, pytest.raises(AppError):
        ServerService._build_auth_material(
            build(auth_method="key", ssh_private_key=pem, ssh_key_passphrase="не та фраза")
        )
    rendered = repr(logs)
    assert body_line not in rendered
    assert "не та фраза" not in rendered
