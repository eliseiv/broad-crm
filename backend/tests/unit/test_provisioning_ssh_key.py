"""Unit: key-ветка провижининга и запуска Ansible (ADR-067 §5, 09-provisioning.md).

Два уровня, оба обязательны по 06-testing-strategy.md:

**`app/infra/ansible.py` (запуск).** `ansible_runner` замокан — реальный SSH не идёт.
Проверяется ровно то, что в проде ломает всё молча:
- inventory key-режима несёт **путь** к файлу и **НЕ содержит `ansible_password`** (иначе
  Ansible уйдёт в `sshpass`-ветку и вход по ключу не состоится);
- `private_data_dir` создаётся в `ANSIBLE_PRIVATE_DATA_ROOT`, а **не** безадресным
  `mkdtemp()` в `/tmp` (в проде корень — `tmpfs`);
- файл ключа имеет права **`0600`** и удаляется в `finally` — **в том числе когда
  `ansible_runner` бросил исключение**;
- материал ключа не попадает ни в лог, ни в `error_message`.

**`app/services/provisioning_service.py` (выбор ветки).** Ветка выбирается ТОЛЬКО по
`auth_method`; неразбираемый/нерасшифровываемый ключ даёт `error_message="SSH key unusable"`
— **отдельно** от `"SSH connection failed"`, иначе оператор чинил бы не то (сеть вместо
ключа).
"""

from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest
import structlog
from app.config import get_settings
from app.infra import ansible as ansible_module
from app.infra.ansible import AnsibleResult, KeyAuth, PasswordAuth, run_install_node_exporter
from app.infra.crypto import encrypt_secret
from app.models.server import ProvisionStatus, ServerAuthMethod
from app.services import provisioning_service
from app.services.provisioning_service import ProvisioningService
from ssh_key_helpers import PASSPHRASE, corrupt_base64_middle, rsa_key, to_openssh

# --- Общая обвязка для app/infra/ansible.py -----------------------------------------


class FakeRunner:
    """Достаточный для `run_install_node_exporter` фейк результата `ansible_runner.run`."""

    def __init__(self, *, rc: int = 0, status: str = "successful", stats: Any = None) -> None:
        self.rc = rc
        self.status = status
        self.stats = stats


@pytest.fixture
def ansible_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Корень приватных данных в tmp + существующий файл плейбука."""
    root = tmp_path / "private-data-root"
    playbook = tmp_path / "playbook.yml"
    playbook.write_text("- hosts: all\n", encoding="utf-8")
    monkeypatch.setenv("ANSIBLE_PRIVATE_DATA_ROOT", str(root))
    monkeypatch.setenv("ANSIBLE_PLAYBOOK_PATH", str(playbook))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def install_fake_ansible_runner(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    *,
    result: FakeRunner | None = None,
    raises: BaseException | None = None,
    observe: Any = None,
) -> None:
    """Подменяет `ansible_runner.run`, фиксируя kwargs и состояние ФС в момент вызова."""
    import sys
    import types

    module = types.ModuleType("ansible_runner")

    def _run(**kwargs: Any) -> FakeRunner:
        calls.append(kwargs)
        if observe is not None:
            observe(kwargs)
        if raises is not None:
            raise raises
        return result or FakeRunner()

    module.run = _run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ansible_runner", module)


def run_key_install(**overrides: Any) -> AnsibleResult:
    params: dict[str, Any] = {
        "target_ip": "10.0.0.10",
        "ssh_user": "root",
        "auth": KeyAuth(private_key_openssh=to_openssh(rsa_key())),
        "exporter_port": 9100,
    }
    params.update(overrides)
    return run_install_node_exporter(**params)


# --- inventory: key-режим не тянет sshpass -------------------------------------------


def test_key_mode_inventory_has_key_path_and_no_ansible_password(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """key-режим: `ansible_ssh_private_key_file` есть, `ansible_password` — НЕТ.

    Наличие `ansible_password` увело бы Ansible в `sshpass`-ветку: вход по ключу не
    состоялся бы, а диагностика указывала бы на «неверный пароль».
    """
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)

    assert run_key_install().success is True

    host_vars = calls[0]["inventory"]["all"]["hosts"]["10.0.0.10"]
    assert "ansible_password" not in host_vars
    assert host_vars["ansible_ssh_private_key_file"]
    assert host_vars["ansible_user"] == "root"
    assert host_vars["ansible_connection"] == "ssh"


def test_password_mode_inventory_keeps_ansible_password_and_no_key_path(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """Парольная ветка не задета ADR-067 (регресс-гейт прежнего поведения)."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)

    run_key_install(auth=PasswordAuth(password="ssh-secret"))

    host_vars = calls[0]["inventory"]["all"]["hosts"]["10.0.0.10"]
    assert host_vars["ansible_password"] == "ssh-secret"
    assert "ansible_ssh_private_key_file" not in host_vars


def test_key_material_never_goes_into_inventory_or_extravars(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """В inventory уходит ТОЛЬКО путь — сам ключ там не появляется ни в каком виде."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)
    material = to_openssh(rsa_key())
    body_line = next(line for line in material.split("\n") if line and "-----" not in line)

    run_key_install(auth=KeyAuth(private_key_openssh=material))

    rendered = repr({k: v for k, v in calls[0].items() if k != "private_data_dir"})
    assert body_line not in rendered


# --- private_data_dir: адресный корень, а не /tmp -------------------------------------


def test_private_data_dir_is_created_inside_configured_root(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """Каталог прогона лежит в `ANSIBLE_PRIVATE_DATA_ROOT` (в проде — `tmpfs`), не в `/tmp`."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)

    run_key_install()

    private_data_dir = Path(calls[0]["private_data_dir"])
    assert private_data_dir.parent == ansible_env
    assert private_data_dir.name.startswith("ansible-runner-")


def test_missing_root_is_created_with_0700(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """Fallback dev-окружения создаёт корень с `0700` (mkdir урезается umask ⇒ chmod)."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)
    assert not ansible_env.exists()

    run_key_install()

    assert stat.S_IMODE(ansible_env.stat().st_mode) == 0o700


def test_existing_root_mode_is_not_touched(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """Режим СУЩЕСТВУЮЩЕГО корня не трогается — иначе fallback затёр бы `0o1777` прод-tmpfs."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)
    ansible_env.mkdir(parents=True)
    os.chmod(ansible_env, 0o1777)

    run_key_install()

    assert stat.S_IMODE(ansible_env.stat().st_mode) == 0o1777


def test_private_data_dir_root_unavailable_returns_failure_not_exception(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """Недоступный корень → `AnsibleResult(success=False)`, а не проброшенный OSError."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)
    monkeypatch.setattr(
        ansible_module.tempfile, "mkdtemp", lambda **_kw: (_ for _ in ()).throw(OSError("denied"))
    )

    result = run_key_install()

    assert result.success is False
    assert result.error_message == "provisioning failed"
    assert calls == []


# --- Файл ключа: 0600, живёт только во время прогона ----------------------------------


def test_key_file_written_with_0600_and_exact_material(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """Во время прогона файл существует, имеет права `0600` и содержит ровно ключ."""
    material = to_openssh(rsa_key())
    observed: dict[str, Any] = {}

    def observe(kwargs: dict[str, Any]) -> None:
        path = Path(
            kwargs["inventory"]["all"]["hosts"]["10.0.0.10"]["ansible_ssh_private_key_file"]
        )
        observed["exists"] = path.is_file()
        observed["mode"] = stat.S_IMODE(path.stat().st_mode)
        observed["content"] = path.read_text(encoding="utf-8")
        observed["dir_mode"] = stat.S_IMODE(Path(kwargs["private_data_dir"]).stat().st_mode)

    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls, observe=observe)

    run_key_install(auth=KeyAuth(private_key_openssh=material))

    assert observed["exists"] is True
    assert observed["mode"] == 0o600
    assert observed["dir_mode"] == 0o700
    assert observed["content"] == material


def test_key_file_and_private_data_dir_removed_after_success(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls)

    run_key_install()

    private_data_dir = Path(calls[0]["private_data_dir"])
    assert not private_data_dir.exists()
    assert list(ansible_env.iterdir()) == []


def test_key_file_removed_even_when_runner_raises(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """`finally` — не украшение: расшифрованный ключ обязан исчезнуть и на исключении."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls, raises=RuntimeError("boom"))

    result = run_key_install()

    assert result.success is False
    assert result.error_message == "provisioning failed"
    private_data_dir = Path(calls[0]["private_data_dir"])
    assert not private_data_dir.exists()
    assert list(ansible_env.iterdir()) == []


def test_key_file_removed_when_playbook_fails(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(
        monkeypatch,
        calls,
        result=FakeRunner(rc=2, status="failed", stats={"dark": {"10.0.0.10": 1}}),
    )

    result = run_key_install()

    assert result.success is False
    assert not Path(calls[0]["private_data_dir"]).exists()


def test_runner_exception_message_carries_no_key_material(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """Текст исключения наружу не идёт: `error_message` фиксирован, лог — без материала."""
    material = to_openssh(rsa_key())
    body_line = next(line for line in material.split("\n") if line and "-----" not in line)
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(monkeypatch, calls, raises=RuntimeError(material))

    with structlog.testing.capture_logs() as logs:
        result = run_key_install(auth=KeyAuth(private_key_openssh=material))

    assert result.error_message == "provisioning failed"
    assert body_line not in repr(logs)


def test_ssh_connection_failure_is_classified_separately_from_key_problems(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    """`dark` от Ansible → `"SSH connection failed"` (сеть/хост), а не «ключ негоден»."""
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(
        monkeypatch,
        calls,
        result=FakeRunner(rc=4, status="failed", stats={"dark": {"10.0.0.10": 1}}),
    )

    assert run_key_install().error_message == "SSH connection failed"


def test_install_failure_is_classified_as_node_exporter_failure(
    monkeypatch: pytest.MonkeyPatch, ansible_env: Path
) -> None:
    calls: list[dict[str, Any]] = []
    install_fake_ansible_runner(
        monkeypatch,
        calls,
        result=FakeRunner(rc=2, status="failed", stats={"failures": {"10.0.0.10": 1}}),
    )

    assert run_key_install().error_message == "node_exporter installation failed"


# --- Выбор ветки в ProvisioningService -------------------------------------------------


@dataclass
class FakeServer:
    id: uuid.UUID
    name: str = "Server 01"
    ip: str = "10.0.0.10"
    ssh_user: str = "root"
    auth_method: str = ServerAuthMethod.key.value
    ssh_password_encrypted: bytes | None = None
    ssh_private_key_encrypted: bytes | None = None
    ssh_key_passphrase_encrypted: bytes | None = None
    exporter_port: int = 9100
    provision_status: str = ProvisionStatus.pending.value
    error_message: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None


class FakeSessionMaker:
    def __call__(self) -> FakeSession:
        return FakeSession()


class FakeRepo:
    server: ClassVar[FakeServer]
    statuses: ClassVar[list[tuple[ProvisionStatus, str | None]]] = []

    def __init__(self, _session: Any) -> None:
        return None

    async def get_by_id(self, server_id: uuid.UUID) -> FakeServer | None:
        return self.server if self.server.id == server_id else None

    async def update_status(
        self,
        server_id: uuid.UUID,
        *,
        status: ProvisionStatus,
        error_message: str | None = None,
    ) -> None:
        FakeRepo.server.provision_status = status.value
        FakeRepo.server.error_message = error_message
        FakeRepo.statuses.append((status, error_message))


@pytest.fixture
def key_server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    server = FakeServer(id=uuid.uuid4())
    FakeRepo.server = server
    FakeRepo.statuses = []
    monkeypatch.setattr(provisioning_service, "ServerRepository", FakeRepo)
    return server


def stub_runner(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    def _run(**kwargs: Any) -> AnsibleResult:
        calls.append(kwargs)
        return AnsibleResult(success=True)

    monkeypatch.setattr(provisioning_service, "run_install_node_exporter", _run)
    monkeypatch.setattr(
        "app.services.provisioning_service.file_sd.write_target", lambda **_kw: None
    )


@pytest.mark.asyncio
async def test_key_server_provisioning_passes_key_auth_to_runner(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    """Ветка выбирается по `auth_method='key'` → в runner уходит `KeyAuth`."""
    key_server.ssh_private_key_encrypted = encrypt_secret(to_openssh(rsa_key()))
    calls: list[dict[str, Any]] = []
    stub_runner(monkeypatch, calls)

    await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(key_server.id)  # type: ignore[arg-type]

    assert isinstance(calls[0]["auth"], KeyAuth)
    assert FakeRepo.statuses == [(ProvisionStatus.installing, None), (ProvisionStatus.online, None)]


@pytest.mark.asyncio
async def test_encrypted_key_passphrase_is_stripped_in_memory_before_runner(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    """Фраза снимается В ПАМЯТИ: в runner уходит НЕзашифрованный OpenSSH-PEM (ADR-067 §5)."""
    from app.domain.ssh_keys import analyze_private_key

    key_server.ssh_private_key_encrypted = encrypt_secret(to_openssh(rsa_key(), PASSPHRASE))
    key_server.ssh_key_passphrase_encrypted = encrypt_secret(PASSPHRASE)
    calls: list[dict[str, Any]] = []
    stub_runner(monkeypatch, calls)

    await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(key_server.id)  # type: ignore[arg-type]

    auth = calls[0]["auth"]
    assert isinstance(auth, KeyAuth)
    assert analyze_private_key(auth.private_key_openssh).is_encrypted is False
    assert PASSPHRASE not in auth.private_key_openssh


@pytest.mark.asyncio
async def test_unparsable_key_yields_ssh_key_unusable_not_connection_failed(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    """Неразбираемый ключ → `error_message="SSH key unusable"`, runner НЕ запускается.

    Отдельная формулировка обязательна: слипшись с `"SSH connection failed"`, она увела
    бы оператора чинить сеть вместо ключа.
    """
    key_server.ssh_private_key_encrypted = encrypt_secret(
        corrupt_base64_middle(to_openssh(rsa_key()))
    )
    calls: list[dict[str, Any]] = []
    stub_runner(monkeypatch, calls)

    await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(key_server.id)  # type: ignore[arg-type]

    assert FakeRepo.statuses[-1] == (ProvisionStatus.error, "SSH key unusable")
    assert calls == []


@pytest.mark.asyncio
async def test_undecryptable_key_yields_ssh_key_unusable(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    """Битая расшифровка (ротация `FERNET_KEY`) → тот же `"SSH key unusable"`."""
    key_server.ssh_private_key_encrypted = b"not-a-fernet-token"
    calls: list[dict[str, Any]] = []
    stub_runner(monkeypatch, calls)

    await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(key_server.id)  # type: ignore[arg-type]

    assert FakeRepo.statuses[-1] == (ProvisionStatus.error, "SSH key unusable")
    assert calls == []


@pytest.mark.asyncio
async def test_missing_key_material_yields_ssh_key_unusable(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    """key-сервер без ключа (недостижимо при живом CHECK) → `error`, а не падение."""
    key_server.ssh_private_key_encrypted = None
    calls: list[dict[str, Any]] = []
    stub_runner(monkeypatch, calls)

    await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(key_server.id)  # type: ignore[arg-type]

    assert FakeRepo.statuses[-1] == (ProvisionStatus.error, "SSH key unusable")


@pytest.mark.asyncio
async def test_wrong_passphrase_in_db_yields_ssh_key_unusable(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    """Рассогласование ключ↔фраза в БД → `"SSH key unusable"` (не «сеть не отвечает»)."""
    key_server.ssh_private_key_encrypted = encrypt_secret(to_openssh(rsa_key(), PASSPHRASE))
    key_server.ssh_key_passphrase_encrypted = encrypt_secret("другая фраза")
    calls: list[dict[str, Any]] = []
    stub_runner(monkeypatch, calls)

    await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(key_server.id)  # type: ignore[arg-type]

    assert FakeRepo.statuses[-1] == (ProvisionStatus.error, "SSH key unusable")
    assert calls == []


@pytest.mark.asyncio
async def test_key_material_absent_from_logs_on_unusable_key(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    material = corrupt_base64_middle(to_openssh(rsa_key()))
    body_line = next(line for line in material.split("\n") if line and "-----" not in line)
    key_server.ssh_private_key_encrypted = encrypt_secret(material)
    stub_runner(monkeypatch, [])

    with structlog.testing.capture_logs() as logs:
        await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(  # type: ignore[arg-type]
            key_server.id
        )

    assert body_line not in repr(logs)


@pytest.mark.asyncio
async def test_password_server_still_uses_password_branch(
    monkeypatch: pytest.MonkeyPatch, key_server: FakeServer
) -> None:
    """Ветка выбирается ТОЛЬКО по `auth_method`, а не по «что не NULL» (регресс-гейт)."""
    key_server.auth_method = ServerAuthMethod.password.value
    key_server.ssh_password_encrypted = encrypt_secret("ssh-secret")
    # Ключ в строке присутствовать не может (CHECK), но если бы присутствовал — ветка та же.
    calls: list[dict[str, Any]] = []
    stub_runner(monkeypatch, calls)

    await ProvisioningService(FakeSessionMaker(), get_settings()).provision_server(key_server.id)  # type: ignore[arg-type]

    assert calls[0]["auth"] == PasswordAuth(password="ssh-secret")
