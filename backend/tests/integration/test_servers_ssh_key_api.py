"""Integration: `POST /api/servers` со способом входа + границы БД (ADR-067, 04-api.md).

Реальный Postgres (`sms_helpers.sms_db`) — иначе главный кейс «CHECK живёт в БД, а не
только в сервисе» непроверяем. Провижининг замокан: `get_provisioning_service`
подменяется фейком, который лишь фиксирует вызов (реальный SSH в тестах запрещён).

Каждый кейс 06-testing-strategy.md §«Серверы — вход по SSH-ключу» — отдельный тест; ключи
генерируются в рантайме (`tests/ssh_key_helpers.py`).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import structlog
from app.models.server import Server
from cryptography.hazmat.primitives.asymmetric import ec
from sms_helpers import build_app, build_principal, client, sms_db
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
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


class FakeProvisioning:
    """Фейк провижининга: реальный SSH не идёт, вызовы фиксируются."""

    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def provision_server(self, server_id: uuid.UUID) -> None:
        self.calls.append(server_id)


def app_with_fake_provisioning(
    sm: async_sessionmaker[AsyncSession], provisioning: FakeProvisioning
) -> Any:
    from app.api import deps

    return build_app(
        sm,
        build_principal(),
        overrides={deps.get_provisioning_service: lambda: provisioning},
    )


def base_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Server 01",
        "ip": f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}",
        "ssh_user": "root",
    }
    body.update(overrides)
    return body


async def load_server(sm: async_sessionmaker[AsyncSession], server_id: uuid.UUID) -> Server:
    async with sm() as session:
        server = await session.get(Server, server_id)
        assert server is not None
        return server


def error_field(payload: dict[str, Any]) -> str:
    """Первое `details[].field` ответа `422` (контракт 04-api.md)."""
    details = payload["error"]["details"]
    assert details, f"422 обязан нести details[] с полем: {payload}"
    return str(details[0]["field"])


# --- 202: успешные ветки --------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_body_without_auth_method_creates_password_server() -> None:
    """Регресс-гейт обратной совместимости: старое тело → `202`, `auth_method='password'`."""
    async with sms_db() as sm:
        provisioning = FakeProvisioning()
        async with client(app_with_fake_provisioning(sm, provisioning)) as http:
            response = await http.post("/api/servers", json=base_body(ssh_password="secret"))

        assert response.status_code == 202
        assert response.json()["auth_method"] == "password"

        server = await load_server(sm, uuid.UUID(response.json()["id"]))
        assert server.auth_method == "password"
        assert server.ssh_password_encrypted is not None
        assert server.ssh_private_key_encrypted is None
        assert server.ssh_key_passphrase_encrypted is None
        assert provisioning.calls == [server.id]


@pytest.mark.asyncio
async def test_key_server_stores_encrypted_key_and_no_password() -> None:
    """`auth_method='key'` → ключ зашифрован, `ssh_password_encrypted IS NULL`."""
    pem = to_openssh(rsa_key())
    async with sms_db() as sm:
        provisioning = FakeProvisioning()
        async with client(app_with_fake_provisioning(sm, provisioning)) as http:
            response = await http.post(
                "/api/servers", json=base_body(auth_method="key", ssh_private_key=pem)
            )

        assert response.status_code == 202
        assert response.json()["auth_method"] == "key"

        server = await load_server(sm, uuid.UUID(response.json()["id"]))
        assert server.ssh_private_key_encrypted is not None
        assert server.ssh_password_encrypted is None
        assert server.ssh_key_passphrase_encrypted is None
        # Plaintext ключа в строке отсутствует (Fernet-ciphertext, а не сырые байты).
        assert pem.encode("utf-8") not in server.ssh_private_key_encrypted


@pytest.mark.asyncio
async def test_pem_armor_without_proc_type_is_accepted_202() -> None:
    """PKCS#1 (`ssh-keygen -m PEM`) и PKCS#8 **без** `Proc-Type` и без фразы → `202`.

    Самый частый формат ввода; реализация, где catch-all ловит его раньше PEM-ветки,
    отдала бы `422` — регресс-гейт шага 1 (ADR-067 §3 п.4).
    """
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            pkcs1 = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_pkcs1(rsa_key())),
            )
            pkcs8 = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_pkcs8(rsa_key())),
            )

        assert pkcs1.status_code == 202, pkcs1.json()
        assert pkcs8.status_code == 202, pkcs8.json()


@pytest.mark.asyncio
async def test_encrypted_key_with_correct_passphrase_stores_both_secrets() -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(
                    auth_method="key",
                    ssh_private_key=to_openssh(rsa_key(), PASSPHRASE),
                    ssh_key_passphrase=PASSPHRASE,
                ),
            )

        assert response.status_code == 202
        server = await load_server(sm, uuid.UUID(response.json()["id"]))
        assert server.ssh_private_key_encrypted is not None
        assert server.ssh_key_passphrase_encrypted is not None
        assert PASSPHRASE.encode("utf-8") not in server.ssh_key_passphrase_encrypted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key_factory",
    [
        lambda: to_openssh(rsa_key(2048)),
        lambda: to_openssh(rsa_key(4096)),
        lambda: to_openssh(ec_key(ec.SECP256R1())),
        lambda: to_openssh(ed25519_key()),
    ],
    ids=["rsa-2048", "rsa-4096", "ecdsa-p256", "ed25519"],
)
async def test_supported_key_types_accepted(key_factory: Any) -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers", json=base_body(auth_method="key", ssh_private_key=key_factory())
            )
        assert response.status_code == 202, response.json()


@pytest.mark.asyncio
async def test_crlf_key_without_trailing_newline_is_stored_normalized() -> None:
    """Ключ с `\\r\\n` и без завершающего `\\n` → `202`, в БД — нормализованная форма."""
    from app.infra.crypto import decrypt_secret

    pem = to_openssh(rsa_key())
    crlf = pem.replace("\n", "\r\n").rstrip()

    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers", json=base_body(auth_method="key", ssh_private_key=crlf)
            )

        assert response.status_code == 202
        server = await load_server(sm, uuid.UUID(response.json()["id"]))
        stored = decrypt_secret(server.ssh_private_key_encrypted)
        assert "\r" not in stored
        assert stored.endswith("\n")
        assert stored == pem


# --- 422: правило «ровно один способ» --------------------------------------------------


@pytest.mark.asyncio
async def test_both_materials_is_422_naming_the_extra_field() -> None:
    """Пароль + ключ сразу → `422` с `field` **лишнего** поля."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(ssh_password="secret", ssh_private_key=to_openssh(rsa_key())),
            )

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_private_key"


@pytest.mark.asyncio
async def test_no_material_at_all_is_422_naming_the_missing_field() -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post("/api/servers", json=base_body())

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_password"


@pytest.mark.asyncio
async def test_key_mode_with_password_field_is_422_on_password() -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(
                    auth_method="key", ssh_private_key=to_openssh(rsa_key()), ssh_password="p"
                ),
            )

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_password"


@pytest.mark.asyncio
async def test_key_mode_without_key_is_422_on_private_key() -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post("/api/servers", json=base_body(auth_method="key"))

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_private_key"


# --- 422: парольная фраза ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_passphrase_is_422_on_passphrase_field() -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(
                    auth_method="key",
                    ssh_private_key=to_openssh(rsa_key(), PASSPHRASE),
                    ssh_key_passphrase="не та фраза",
                ),
            )

        assert response.status_code == 422
        payload = response.json()
        assert error_field(payload) == "ssh_key_passphrase"
        assert payload["error"]["message"] == "Неверная парольная фраза"


@pytest.mark.asyncio
async def test_missing_passphrase_for_encrypted_key_is_422_on_passphrase_field() -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(
                    auth_method="key", ssh_private_key=to_openssh(rsa_key(), PASSPHRASE)
                ),
            )

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_key_passphrase"


@pytest.mark.asyncio
async def test_extra_passphrase_for_unencrypted_key_is_422_on_passphrase_field() -> None:
    """Гейт шага 2: `cryptography` 43.x лишний пароль может молча проигнорировать."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(
                    auth_method="key",
                    ssh_private_key=to_openssh(rsa_key()),
                    ssh_key_passphrase=PASSPHRASE,
                ),
            )

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_key_passphrase"


@pytest.mark.asyncio
@pytest.mark.parametrize("passphrase", ["", "   "], ids=["empty", "whitespace"])
async def test_blank_passphrase_is_422_on_passphrase_field(passphrase: str) -> None:
    """`ssh_key_passphrase: ''`/`'   '` → `422 field=ssh_key_passphrase` (не «не задана»)."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(
                    auth_method="key",
                    ssh_private_key=to_openssh(rsa_key()),
                    ssh_key_passphrase=passphrase,
                ),
            )

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_key_passphrase"


# --- 422: содержимое ключа ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key_factory",
    [
        lambda: "это точно не приватный ключ",
        lambda: public_openssh(rsa_key()),
        lambda: corrupt_base64_middle(to_openssh(rsa_key())),
    ],
    ids=["garbage", "public-key", "corrupted-base64-middle"],
)
async def test_unparsable_key_is_422_on_private_key(key_factory: Any) -> None:
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers", json=base_body(auth_method="key", ssh_private_key=key_factory())
            )

        assert response.status_code == 422
        assert error_field(response.json()) == "ssh_private_key"


@pytest.mark.asyncio
async def test_dsa_key_is_422_unsupported_type() -> None:
    """DSA-2048 отвергается **на валидации** — иначе упал бы уже на провижининге."""
    async with sms_db() as sm:
        provisioning = FakeProvisioning()
        async with client(app_with_fake_provisioning(sm, provisioning)) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_pkcs8(dsa_key(2048))),
            )

        assert response.status_code == 422
        payload = response.json()
        assert error_field(payload) == "ssh_private_key"
        assert payload["error"]["message"] == "Тип ключа не поддерживается"
        # Сервер не создан и провижининг не запускался.
        assert provisioning.calls == []


@pytest.mark.asyncio
async def test_ec_curve_outside_whitelist_is_422() -> None:
    """EC вне P-256/384/521 (secp256k1) → `422 field=ssh_private_key`."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            response = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_pkcs8(ec_key(ec.SECP256K1()))),
            )

        assert response.status_code == 422
        payload = response.json()
        assert error_field(payload) == "ssh_private_key"
        assert payload["error"]["message"] == "Тип ключа не поддерживается"


@pytest.mark.asyncio
async def test_key_over_ssh_key_max_bytes_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Превышение `SSH_KEY_MAX_BYTES` → `422` (проверка ДО разбора)."""
    from app.config import get_settings

    monkeypatch.setenv("SSH_KEY_MAX_BYTES", "512")
    get_settings.cache_clear()
    try:
        async with sms_db() as sm:
            async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
                response = await http.post(
                    "/api/servers",
                    json=base_body(auth_method="key", ssh_private_key=to_openssh(rsa_key(4096))),
                )

            assert response.status_code == 422
            assert error_field(response.json()) == "ssh_private_key"
    finally:
        get_settings.cache_clear()


# --- Секреты не в ответах и не в логах ----------------------------------------------------


@pytest.mark.asyncio
async def test_202_body_and_logs_carry_no_key_material() -> None:
    """Тело `202` — только `auth_method`; ни ответ, ни structlog не несут ключ/фразу."""
    pem = to_openssh(rsa_key(), PASSPHRASE)
    body_line = next(line for line in pem.split("\n") if line and "-----" not in line)

    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            with structlog.testing.capture_logs() as logs:
                response = await http.post(
                    "/api/servers",
                    json=base_body(
                        auth_method="key", ssh_private_key=pem, ssh_key_passphrase=PASSPHRASE
                    ),
                )

        assert response.status_code == 202
        payload = response.json()
        assert payload["auth_method"] == "key"
        for forbidden in ("ssh_private_key", "ssh_key_passphrase", "ssh_password"):
            assert forbidden not in payload
        assert body_line not in response.text
        assert body_line not in repr(logs)
        assert PASSPHRASE not in repr(logs)


@pytest.mark.asyncio
async def test_422_branch_does_not_leak_cryptography_text_to_response_or_logs() -> None:
    """Ветка `422` тоже молчит: текст исключения `cryptography` наружу не идёт."""
    pem = to_openssh(rsa_key(), PASSPHRASE)
    body_line = next(line for line in pem.split("\n") if line and "-----" not in line)

    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            with structlog.testing.capture_logs() as logs:
                response = await http.post(
                    "/api/servers",
                    json=base_body(
                        auth_method="key", ssh_private_key=pem, ssh_key_passphrase="не та фраза"
                    ),
                )

        assert response.status_code == 422
        assert body_line not in response.text
        assert "не та фраза" not in response.text
        assert body_line not in repr(logs)


@pytest.mark.asyncio
async def test_key_material_absent_from_list_and_detail_responses() -> None:
    """`GET /api/servers` отдаёт `auth_method`, но не материал."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_openssh(rsa_key())),
            )
            listing = await http.get("/api/servers")

        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["auth_method"] == "key"
        for forbidden in ("ssh_private_key", "ssh_key_passphrase", "ssh_password"):
            assert forbidden not in item


# --- Reveal-эндпоинты ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reveal_ssh_password_on_key_server_is_404_secret_not_set() -> None:
    """У key-сервера пароля нет → `404 secret_not_set` (04-api.md, ADR-067 §4)."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_openssh(rsa_key())),
            )
            server_id = created.json()["id"]
            response = await http.get(f"/api/servers/{server_id}/ssh-password")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "secret_not_set"


@pytest.mark.asyncio
async def test_reveal_ssh_password_on_password_server_is_200() -> None:
    """Парольная ветка reveal не ослаблена (регресс-гейт ADR-035)."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post("/api/servers", json=base_body(ssh_password="ssh-secret"))
            server_id = created.json()["id"]
            response = await http.get(f"/api/servers/{server_id}/ssh-password")

        assert response.status_code == 200
        assert response.json()["value"] == "ssh-secret"
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["ssh-key", "ssh-key-passphrase"], ids=["key", "passphrase"])
async def test_reveal_endpoints_for_key_material_do_not_exist(suffix: str) -> None:
    """Контракт-гейт: `/ssh-key` и `/ssh-key-passphrase` **не существуют** → `404`.

    Приватный ключ и парольная фраза — write-only by design (ADR-067 §4). Тест стоит
    против «доброжелательного» добавления reveal в будущем.
    """
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_openssh(rsa_key())),
            )
            server_id = created.json()["id"]
            response = await http.get(f"/api/servers/{server_id}/{suffix}")

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_reveal_endpoints_absent_from_openapi_schema() -> None:
    """Их нет и в схеме — не «спрятаны», а отсутствуют как роуты."""
    async with sms_db() as sm:
        app = app_with_fake_provisioning(sm, FakeProvisioning())
        paths = app.openapi()["paths"]

    assert "/api/servers/{server_id}/ssh-password" in paths
    assert "/api/servers/{server_id}/ssh-key" not in paths
    assert "/api/servers/{server_id}/ssh-key-passphrase" not in paths


# --- PATCH не трогает способ входа ---------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_ignores_auth_method_and_secret_fields() -> None:
    """`PATCH` с полями способа входа их игнорирует: `auth_method` в БД не меняется."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_openssh(rsa_key())),
            )
            server_id = created.json()["id"]

            response = await http.patch(
                f"/api/servers/{server_id}",
                json={
                    "name": "Renamed",
                    "auth_method": "password",
                    "ssh_password": "hacked",
                    "ssh_private_key": to_openssh(rsa_key()),
                },
            )

        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
        assert response.json()["auth_method"] == "key"

        server = await load_server(sm, uuid.UUID(server_id))
        assert server.auth_method == "key"
        assert server.ssh_password_encrypted is None
        assert server.ssh_private_key_encrypted is not None


# --- Граница целостности живёт в БД ----------------------------------------------------------


@pytest.mark.asyncio
async def test_check_constraint_rejects_password_plus_key_state() -> None:
    """Прямой `UPDATE` в «пароль + ключ» → `IntegrityError` (CHECK `ck_servers_auth_material`).

    Ключевой кейс: граница обязана жить **в БД**, а не только в сервисе — иначе любой
    путь мимо `ServerService` (миграция, ручной фикс, будущий эндпоинт) породил бы строку,
    у которой «оба способа сразу», и провижининг выбирал бы материал наугад.
    """
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_openssh(rsa_key())),
            )
            server_id = created.json()["id"]

        async with sm() as session:
            with pytest.raises(IntegrityError) as exc:
                await session.execute(
                    sa_text(
                        "UPDATE servers SET ssh_password_encrypted = :pwd WHERE id = :id"
                    ).bindparams(pwd=b"ciphertext", id=uuid.UUID(server_id))
                )
                await session.commit()
            assert "ck_servers_auth_material" in str(exc.value)
            await session.rollback()


@pytest.mark.asyncio
async def test_check_constraint_rejects_key_method_without_key_material() -> None:
    """«key без ключа» тоже отвергается БД."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post("/api/servers", json=base_body(ssh_password="secret"))
            server_id = created.json()["id"]

        async with sm() as session:
            with pytest.raises(IntegrityError) as exc:
                await session.execute(
                    sa_text(
                        "UPDATE servers SET auth_method = 'key', "
                        "ssh_password_encrypted = NULL WHERE id = :id"
                    ).bindparams(id=uuid.UUID(server_id))
                )
                await session.commit()
            assert "ck_servers_auth_material" in str(exc.value)
            await session.rollback()


@pytest.mark.asyncio
async def test_check_constraint_rejects_password_method_without_password() -> None:
    """«password без пароля» — третий запрещённый вариант."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post("/api/servers", json=base_body(ssh_password="secret"))
            server_id = created.json()["id"]

        async with sm() as session:
            with pytest.raises(IntegrityError):
                await session.execute(
                    sa_text(
                        "UPDATE servers SET ssh_password_encrypted = NULL WHERE id = :id"
                    ).bindparams(id=uuid.UUID(server_id))
                )
                await session.commit()
            await session.rollback()


@pytest.mark.asyncio
async def test_check_constraint_rejects_unknown_auth_method() -> None:
    """Третьего способа входа не существует — БД отвергает `auth_method='kerberos'`.

    Имя нарушенного констрейнта здесь НЕ фиксируется: неизвестный способ нарушает
    **оба** CHECK (`ck_servers_auth_method` — по whitelist значения,
    `ck_servers_auth_material` — потому что не подходит ни под одну ветку), а какой из
    них Postgres назовёт первым, деталь реализации. Значим факт отказа.
    """
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            created = await http.post("/api/servers", json=base_body(ssh_password="secret"))
            server_id = created.json()["id"]

        async with sm() as session:
            with pytest.raises(IntegrityError) as exc:
                await session.execute(
                    sa_text(
                        "UPDATE servers SET auth_method = 'kerberos' WHERE id = :id"
                    ).bindparams(id=uuid.UUID(server_id))
                )
                await session.commit()
            assert "CheckViolationError" in str(exc.value)
            await session.rollback()


@pytest.mark.asyncio
async def test_auth_method_whitelist_check_exists_in_schema() -> None:
    """`ck_servers_auth_method` присутствует в схеме именно как CHECK на whitelist.

    Отдельный кейс, потому что предыдущий (по конструкции) не может назвать констрейнт:
    здесь имя и определение читаются из `pg_constraint`.
    """
    async with sms_db() as sm, sm() as session:
        rows = (
            await session.execute(
                sa_text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'servers'::regclass AND contype = 'c'"
                )
            )
        ).all()

    defs = dict(rows)
    assert "ck_servers_auth_method" in defs
    assert "'password'" in defs["ck_servers_auth_method"]
    assert "'key'" in defs["ck_servers_auth_method"]
    assert "ck_servers_auth_material" in defs


@pytest.mark.asyncio
async def test_key_server_row_is_representable_only_with_key_material() -> None:
    """Позитивная сторона CHECK: строка key-сервера в БД проходит без пароля."""
    async with sms_db() as sm:
        async with client(app_with_fake_provisioning(sm, FakeProvisioning())) as http:
            await http.post(
                "/api/servers",
                json=base_body(auth_method="key", ssh_private_key=to_openssh(rsa_key())),
            )

        async with sm() as session:
            rows = (await session.execute(select(Server))).scalars().all()
        assert len(rows) == 1
        assert rows[0].auth_method == "key"
        assert rows[0].ssh_password_encrypted is None
