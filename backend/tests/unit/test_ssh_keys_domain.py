"""Unit: 4-шаговая процедура разбора приватного SSH-ключа (ADR-067 §3 п.4).

Каждый кейс 06-testing-strategy.md — **отдельный тест**; ключи генерируются в рантайме
(`tests/ssh_key_helpers.py`), в репозиторий не коммитятся.

Проверяется ровно то, что делает процедуру не-наивной:
1. **шаг 1** — структурное `is_encrypted` (PEM-armor **без** `Proc-Type` → незашифрован;
   битая base64-середина при целых BEGIN/END → отказ);
2. **шаг 2** — кросс-проверка `is_encrypted` ↔ наличие фразы (лишняя фраза к
   незашифрованному ключу обязана давать `422` НАШЕЙ проверкой: `cryptography` 43.x может
   молча её проигнорировать);
3. **шаг 3** — ветка отказа выбирается по `is_encrypted`, а не по тексту исключения;
4. **шаг 4** — whitelist типов: DSA и EC вне P-256/384/521 отвергаются **на валидации**.
"""

from __future__ import annotations

import pytest
from app.domain.ssh_keys import (
    FIELD_PASSPHRASE,
    FIELD_PRIVATE_KEY,
    MSG_PASSPHRASE_NOT_NEEDED,
    MSG_PASSPHRASE_REQUIRED,
    MSG_PASSPHRASE_WRONG,
    MSG_UNPARSABLE,
    MSG_UNSUPPORTED_TYPE,
    SshKeyError,
    analyze_private_key,
    normalize_private_key,
    to_openssh_unencrypted,
    validate_private_key,
)
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

# --- Шаг 1: структурное определение is_encrypted ------------------------------------


def test_analyze_openssh_unencrypted_is_not_encrypted() -> None:
    structure = analyze_private_key(to_openssh(rsa_key()))
    assert structure.is_encrypted is False
    assert structure.is_openssh is True


def test_analyze_openssh_encrypted_reads_ciphername_from_blob() -> None:
    """`is_encrypted` берётся из поля `ciphername` формата `openssh-key-v1`, не из regex."""
    structure = analyze_private_key(to_openssh(rsa_key(), PASSPHRASE))
    assert structure.is_encrypted is True
    assert structure.is_openssh is True


def test_analyze_pkcs8_encrypted_label_is_encrypted() -> None:
    structure = analyze_private_key(to_pkcs8(rsa_key(), PASSPHRASE))
    assert structure.is_encrypted is True
    assert structure.is_openssh is False


def test_analyze_legacy_pem_proc_type_header_is_encrypted() -> None:
    """Legacy PEM с `Proc-Type: 4,ENCRYPTED` (PKCS#1 с фразой) — зашифрован."""
    pem = to_pkcs1(rsa_key(), PASSPHRASE)
    assert "Proc-Type: 4,ENCRYPTED" in pem or "ENCRYPTED PRIVATE KEY" in pem
    assert analyze_private_key(pem).is_encrypted is True


def test_analyze_public_key_rejected() -> None:
    """`ssh-rsa AAAA…` — не PEM-armor, ловится шагом 1 (частая ошибка ввода)."""
    with pytest.raises(SshKeyError) as exc:
        analyze_private_key(public_openssh(rsa_key()))
    assert exc.value.field == FIELD_PRIVATE_KEY
    assert exc.value.message == MSG_UNPARSABLE


def test_analyze_arbitrary_text_rejected() -> None:
    with pytest.raises(SshKeyError) as exc:
        analyze_private_key("совершенно не ключ\n")
    assert exc.value.field == FIELD_PRIVATE_KEY


def test_analyze_certificate_armor_rejected() -> None:
    """Armor есть, но метка не оканчивается на `PRIVATE KEY` — отказ шага 1."""
    with pytest.raises(SshKeyError) as exc:
        analyze_private_key("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    assert exc.value.field == FIELD_PRIVATE_KEY


# --- Шаг 1: PEM-armor БЕЗ Proc-Type → 202 (регресс-гейт, ADR-067 §3 п.4) ------------


def test_pkcs1_rsa_without_proc_type_is_accepted_unencrypted() -> None:
    """`-----BEGIN RSA PRIVATE KEY-----` (`ssh-keygen -m PEM`) **без** фразы → принимается.

    Самый частый формат ввода. Реализация, где catch-all ловит его раньше PEM-ветки,
    отдала бы `422` — этот тест обязан её ловить.
    """
    pem = to_pkcs1(rsa_key())
    assert pem.startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert "Proc-Type" not in pem

    structure = analyze_private_key(pem)
    assert structure.is_encrypted is False
    validate_private_key(pem, None)  # не бросает


def test_pkcs8_plain_without_proc_type_is_accepted_unencrypted() -> None:
    """`-----BEGIN PRIVATE KEY-----` (PKCS#8) **без** фразы → принимается."""
    pem = to_pkcs8(rsa_key())
    assert pem.startswith("-----BEGIN PRIVATE KEY-----")

    assert analyze_private_key(pem).is_encrypted is False
    validate_private_key(pem, None)


def test_sec1_ec_private_key_without_proc_type_is_accepted() -> None:
    """`-----BEGIN EC PRIVATE KEY-----` (SEC1) — та же ветка «незашифрованный PEM»."""
    pem = to_pkcs1(ec_key(ec.SECP256R1()))
    assert pem.startswith("-----BEGIN EC PRIVATE KEY-----")
    validate_private_key(pem, None)


# --- Шаг 1: битая base64-середина при целых заголовках ------------------------------


def test_openssh_corrupted_base64_middle_rejected() -> None:
    """Заголовки BEGIN/END целы, тело испорчено → `422` (гейт против regex-валидации)."""
    broken = corrupt_base64_middle(to_openssh(rsa_key()))
    assert broken.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert broken.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----")

    with pytest.raises(SshKeyError) as exc:
        validate_private_key(broken, None)
    assert exc.value.field == FIELD_PRIVATE_KEY
    assert exc.value.message == MSG_UNPARSABLE


def test_pkcs8_corrupted_base64_middle_rejected() -> None:
    broken = corrupt_base64_middle(to_pkcs8(rsa_key()))
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(broken, None)
    assert exc.value.field == FIELD_PRIVATE_KEY
    assert exc.value.message == MSG_UNPARSABLE


def test_openssh_truncated_body_rejected() -> None:
    """Обрезанное тело (магия есть, поле ciphername не полное) → отказ, а не IndexError."""
    import base64

    blob = base64.b64encode(b"openssh-key-v1\x00\x00\x00").decode("ascii")
    text = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{blob}\n-----END OPENSSH PRIVATE KEY-----\n"
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(text, None)
    assert exc.value.field == FIELD_PRIVATE_KEY


def test_openssh_armor_without_magic_rejected() -> None:
    import base64

    blob = base64.b64encode(b"not-an-openssh-blob-at-all").decode("ascii")
    text = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{blob}\n-----END OPENSSH PRIVATE KEY-----\n"
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(text, None)
    assert exc.value.field == FIELD_PRIVATE_KEY


# --- Шаг 2: кросс-проверка is_encrypted ↔ парольная фраза ---------------------------


def test_passphrase_given_to_unencrypted_key_is_rejected_by_our_check() -> None:
    """Лишняя фраза → `422 field=ssh_key_passphrase`.

    Ключевой гейт шага 2: `cryptography` 43.x лишний пароль к незашифрованному ключу
    может **молча проигнорировать**, поэтому исход обязан задавать НАШ код.
    """
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(to_openssh(rsa_key()), PASSPHRASE)
    assert exc.value.field == FIELD_PASSPHRASE
    assert exc.value.message == MSG_PASSPHRASE_NOT_NEEDED


def test_passphrase_given_to_unencrypted_pkcs1_is_rejected() -> None:
    """Тот же исход для PKCS#1 — ветка PEM, а не OpenSSH."""
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(to_pkcs1(rsa_key()), PASSPHRASE)
    assert exc.value.field == FIELD_PASSPHRASE
    assert exc.value.message == MSG_PASSPHRASE_NOT_NEEDED


def test_passphrase_missing_for_encrypted_key_is_rejected() -> None:
    """Недостающая фраза → `422 field=ssh_key_passphrase` (шаг 2, ДО загрузки)."""
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(to_openssh(rsa_key(), PASSPHRASE), None)
    assert exc.value.field == FIELD_PASSPHRASE
    assert exc.value.message == MSG_PASSPHRASE_REQUIRED


def test_passphrase_missing_for_encrypted_pkcs8_is_rejected() -> None:
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(to_pkcs8(rsa_key(), PASSPHRASE), None)
    assert exc.value.field == FIELD_PASSPHRASE
    assert exc.value.message == MSG_PASSPHRASE_REQUIRED


# --- Шаг 3: ветка отказа выбирается по is_encrypted ---------------------------------


def test_wrong_passphrase_maps_to_passphrase_field() -> None:
    """Неверная фраза → `422 field=ssh_key_passphrase`, «Неверная парольная фраза»."""
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(to_openssh(rsa_key(), PASSPHRASE), "не та фраза")
    assert exc.value.field == FIELD_PASSPHRASE
    assert exc.value.message == MSG_PASSPHRASE_WRONG


def test_wrong_passphrase_pkcs8_maps_to_passphrase_field() -> None:
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(to_pkcs8(rsa_key(), PASSPHRASE), "не та фраза")
    assert exc.value.field == FIELD_PASSPHRASE
    assert exc.value.message == MSG_PASSPHRASE_WRONG


def test_error_message_never_leaks_cryptography_text() -> None:
    """Наружу идут ТОЛЬКО фиксированные сообщения контракта (ADR-067 §3 п.4).

    Текст исключения `cryptography` («Incorrect password?», «Could not deserialize…»)
    не контракт библиотеки и может нести фрагменты материала.
    """
    fixed = {
        MSG_UNPARSABLE,
        MSG_UNSUPPORTED_TYPE,
        MSG_PASSPHRASE_NOT_NEEDED,
        MSG_PASSPHRASE_REQUIRED,
        MSG_PASSPHRASE_WRONG,
    }
    cases: list[tuple[str, str | None]] = [
        (to_openssh(rsa_key(), PASSPHRASE), "не та фраза"),
        (to_openssh(rsa_key(), PASSPHRASE), None),
        (to_openssh(rsa_key()), PASSPHRASE),
        (corrupt_base64_middle(to_pkcs8(rsa_key())), None),
        (public_openssh(rsa_key()), None),
        (to_pkcs8(dsa_key()), None),
    ]
    for text, passphrase in cases:
        with pytest.raises(SshKeyError) as exc:
            validate_private_key(text, passphrase)
        assert exc.value.message in fixed, exc.value.message


# --- Шаг 4: whitelist типов ключей --------------------------------------------------


@pytest.mark.parametrize("bits", [2048, 4096])
def test_rsa_2048_and_4096_accepted(bits: int) -> None:
    validate_private_key(to_openssh(rsa_key(bits)), None)


@pytest.mark.parametrize(
    "curve",
    [ec.SECP256R1(), ec.SECP384R1(), ec.SECP521R1()],
    ids=["P-256", "P-384", "P-521"],
)
def test_ecdsa_nist_curves_accepted(curve: ec.EllipticCurve) -> None:
    validate_private_key(to_openssh(ec_key(curve)), None)


def test_ed25519_accepted() -> None:
    validate_private_key(to_openssh(ed25519_key()), None)


def test_dsa_rejected_at_validation_not_at_provisioning() -> None:
    """DSA-2048 → `422 field=ssh_private_key` «Тип ключа не поддерживается».

    Регресс-гейт: без шага 4 ключ прошёл бы форму и упал уже на провижининге (DSA
    deprecated в `cryptography` 43 и отключён в OpenSSH ≥ 7.0).
    """
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(to_pkcs8(dsa_key(2048)), None)
    assert exc.value.field == FIELD_PRIVATE_KEY
    assert exc.value.message == MSG_UNSUPPORTED_TYPE


def test_dsa_pkcs1_armor_rejected_with_type_message_not_unparsable() -> None:
    """`DSA PRIVATE KEY` разбирается (шаг 1 его не режет) и отвергается ПО ТИПУ.

    Иначе сообщение было бы «не удалось разобрать», что дезориентирует: ключ валиден,
    не поддержан именно его тип.
    """
    pem = to_pkcs1(dsa_key(2048))
    assert pem.startswith("-----BEGIN DSA PRIVATE KEY-----")
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(pem, None)
    assert exc.value.message == MSG_UNSUPPORTED_TYPE


def test_ec_secp256k1_outside_whitelist_rejected() -> None:
    """EC вне P-256/384/521 (secp256k1) → `422 field=ssh_private_key` по типу."""
    pem = to_pkcs8(ec_key(ec.SECP256K1()))
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(pem, None)
    assert exc.value.field == FIELD_PRIVATE_KEY
    assert exc.value.message == MSG_UNSUPPORTED_TYPE


def test_ec_secp192r1_outside_whitelist_rejected() -> None:
    """Ещё одна EC вне whitelist — слабая P-192."""
    pem = to_pkcs8(ec_key(ec.SECP192R1()))
    with pytest.raises(SshKeyError) as exc:
        validate_private_key(pem, None)
    assert exc.value.message == MSG_UNSUPPORTED_TYPE


# --- Нормализация (ADR-067 §3 п.3) --------------------------------------------------


def test_normalize_converts_crlf_and_appends_trailing_newline() -> None:
    """`CRLF → LF`, срез хвостовых пробелов, гарантированный завершающий `\\n`."""
    pem = to_openssh(rsa_key())
    crlf = pem.replace("\n", "\r\n").rstrip()  # CRLF и БЕЗ завершающего перевода строки
    assert "\r\n" in crlf and not crlf.endswith("\n")

    normalized = normalize_private_key(crlf)
    assert "\r" not in normalized
    assert normalized.endswith("\n")
    assert normalized == pem


def test_normalize_lone_cr_converted() -> None:
    assert normalize_private_key("a\rb") == "a\nb\n"


def test_normalize_empty_input_stays_empty() -> None:
    """Пустой ввод не превращается в одинокий `\\n` (иначе он «прошёл бы» дальше)."""
    assert normalize_private_key("   \n\t ") == ""


def test_crlf_key_validates_after_normalization() -> None:
    """Ключ с `\\r\\n` и без завершающего `\\n` валиден ПОСЛЕ нормализации."""
    crlf = to_openssh(rsa_key()).replace("\n", "\r\n").rstrip()
    validate_private_key(normalize_private_key(crlf), None)


# --- Пере-сериализация для провижининга (ADR-067 §5 п.2) ----------------------------


def test_to_openssh_unencrypted_strips_passphrase_in_memory() -> None:
    """Зашифрованный ключ → НЕзашифрованный OpenSSH-PEM (фраза снята в памяти)."""
    material = to_openssh_unencrypted(to_openssh(rsa_key(), PASSPHRASE), PASSPHRASE)
    assert material.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    # Результат обязан быть НЕзашифрованным — иначе Ansible запросил бы фразу интерактивно.
    assert analyze_private_key(material).is_encrypted is False
    assert PASSPHRASE not in material


def test_to_openssh_unencrypted_converts_pkcs1_to_openssh() -> None:
    """PKCS#1 (`ssh-keygen -m PEM`) конвертируется в OpenSSH-формат для Ansible."""
    material = to_openssh_unencrypted(to_pkcs1(rsa_key()), None)
    assert material.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")


def test_to_openssh_unencrypted_propagates_key_error() -> None:
    """Неразбираемый ключ → `SshKeyError` (провижининг переведёт сервер в `error`)."""
    with pytest.raises(SshKeyError):
        to_openssh_unencrypted(corrupt_base64_middle(to_openssh(rsa_key())), None)
