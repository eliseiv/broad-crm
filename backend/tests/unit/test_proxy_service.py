"""Unit-тесты сервисного слоя прокси: create/list/status/delete, PATCH-семантика
пароля/логина/re-check, reorder-прецеденция (modules/proxies, 04-api.md#proxies).

Фейковые репозиторий/монитор (без БД/сети, реальная крипта из conftest FERNET_KEY):
  - create: шифрование пароля Fernet (round-trip) только если задан; None → NULL;
    статус pending; немедленная фоновая проверка; has_password из password_encrypted;
    пароль отсутствует в ответе;
  - PATCH password: не передан = не менять; null/"" = очистить; непустой → re-encrypt;
  - PATCH username: не передан = не менять; null/"" = убрать; значение = установить;
  - re-check при смене proxy_type/host/port/username/password (prev='pending'); только
    name → без re-check;
  - reorder-прецеденция: несуществующий id → 404 (ДО полноты); все существуют, но не
    полная перестановка → 422; успех присваивает position 0..N-1;
  - get_status/delete → 404 при отсутствии/повторе.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from app.errors import AppError
from app.infra.crypto import decrypt_secret, encrypt_secret
from app.models.proxy import ProxyStatus, ProxyType
from app.schemas.proxy import ProxyCreateRequest, ProxyUpdateRequest
from app.services.proxy_service import ProxyService

OLD_PASSWORD = "old-s3cr3t"
NEW_PASSWORD = "new-s3cr3t"


class FakeProxy:
    def __init__(
        self,
        *,
        name: str = "DE Residential",
        proxy_type: str = ProxyType.socks5.value,
        host: str = "proxy.example.com",
        port: int = 1080,
        username: str | None = "user01",
        password: str | None = OLD_PASSWORD,
        check_status: str = ProxyStatus.working.value,
        position: int = 0,
    ) -> None:
        now = datetime.now(UTC)
        self.id = uuid.uuid4()
        self.name = name
        self.proxy_type = proxy_type
        self.host = host
        self.port = port
        self.username = username
        self.password_encrypted = encrypt_secret(password) if password is not None else None
        self.check_status = check_status
        self.error_message: str | None = "Прокси недоступен" if check_status == "error" else None
        self.position = position
        self.last_checked_at: datetime | None = now
        self.created_at = now
        self.updated_at = now


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj: object) -> None:
        return None


class FakeProxyRepo:
    def __init__(self, proxies: list[FakeProxy] | None = None) -> None:
        self._session = FakeSession()
        self.proxies: dict[uuid.UUID, FakeProxy] = {p.id: p for p in (proxies or [])}
        self.reordered: list[uuid.UUID] | None = None
        self.captured_encrypted: bytes | None = None

    @property
    def session(self) -> FakeSession:
        return self._session

    async def create(
        self,
        *,
        name: str,
        proxy_type: str,
        host: str,
        port: int,
        username: str | None,
        password_encrypted: bytes | None,
    ) -> FakeProxy:
        self.captured_encrypted = password_encrypted
        proxy = FakeProxy(
            name=name,
            proxy_type=proxy_type,
            host=host,
            port=port,
            username=username,
            password=None,
            check_status=ProxyStatus.pending.value,
        )
        proxy.password_encrypted = password_encrypted
        self.proxies[proxy.id] = proxy
        return proxy

    async def list_all(self) -> list[FakeProxy]:
        return list(self.proxies.values())

    async def get_by_id(self, proxy_id: uuid.UUID) -> FakeProxy | None:
        return self.proxies.get(proxy_id)

    async def all_ids(self) -> set[uuid.UUID]:
        return set(self.proxies)

    async def reorder(self, ordered_ids: list[uuid.UUID]) -> None:
        self.reordered = list(ordered_ids)
        for index, proxy_id in enumerate(ordered_ids):
            self.proxies[proxy_id].position = index

    async def delete_by_id(self, proxy_id: uuid.UUID) -> bool:
        return self.proxies.pop(proxy_id, None) is not None


class FakeMonitor:
    def __init__(self) -> None:
        self.checked: list[uuid.UUID] = []

    async def check_one(self, proxy_id: uuid.UUID) -> None:
        self.checked.append(proxy_id)


def _service(repo: FakeProxyRepo, monitor: FakeMonitor) -> ProxyService:
    return ProxyService(repository=cast(Any, repo), monitor=cast(Any, monitor))


# ------------------------------------------------------------------------- create
async def test_create_encrypts_password_fernet_roundtrip_and_has_password() -> None:
    repo = FakeProxyRepo()
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.create_proxy(
        ProxyCreateRequest(
            name="DE Residential",
            proxy_type=ProxyType.socks5,
            host="proxy.example.com",
            port=1080,
            username="user01",
            password=OLD_PASSWORD,
        )
    )
    await asyncio.sleep(0)

    # Пароль зашифрован Fernet и расшифровывается обратно (round-trip); ciphertext != plaintext.
    assert repo.captured_encrypted is not None
    assert repo.captured_encrypted != OLD_PASSWORD.encode("utf-8")
    assert decrypt_secret(repo.captured_encrypted) == OLD_PASSWORD

    # Ответ: has_password=true, статус pending, пароль (в любом виде) отсутствует.
    assert item.has_password is True
    assert item.check_status == ProxyStatus.pending
    assert item.username == "user01"
    dumped = item.model_dump_json()
    assert OLD_PASSWORD not in dumped
    assert "password_encrypted" not in dumped
    assert "password" not in item.model_dump()
    assert repo.session.commits == 1

    # Немедленная фоновая проверка запущена для созданного прокси (fire-and-forget).
    assert monitor.checked == [item.id]


async def test_create_without_password_has_password_false() -> None:
    repo = FakeProxyRepo()
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.create_proxy(
        ProxyCreateRequest(
            name="No auth",
            proxy_type=ProxyType.http,
            host="host",
            port=8080,
        )
    )

    assert repo.captured_encrypted is None  # password_encrypted = NULL
    assert item.has_password is False
    assert item.username is None


async def test_create_empty_password_and_username_normalized_to_none() -> None:
    repo = FakeProxyRepo()
    service = _service(repo, FakeMonitor())

    item = await service.create_proxy(
        ProxyCreateRequest(
            name="Empty",
            proxy_type=ProxyType.https,
            host="host",
            port=443,
            username="",
            password="",
        )
    )

    # "" → без логина/пароля (04-api.md#post-apiproxies).
    assert repo.captured_encrypted is None
    assert item.has_password is False
    assert item.username is None


# --------------------------------------------------------------------------- list
async def test_list_proxies_returns_items_without_password() -> None:
    proxy = FakeProxy()
    repo = FakeProxyRepo([proxy])
    service = _service(repo, FakeMonitor())

    listed = await service.list_proxies()

    assert len(listed.items) == 1
    assert listed.items[0].has_password is True
    assert OLD_PASSWORD not in listed.model_dump_json()


# ------------------------------------------------------------------ PATCH: пароль
async def test_update_password_not_passed_keeps_secret_no_recheck() -> None:
    proxy = FakeProxy(check_status=ProxyStatus.working.value)
    original_cipher = proxy.password_encrypted
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    # Только name: пароль не передан → не менять; re-check НЕ запускается.
    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(name="Renamed"))
    await asyncio.sleep(0)

    assert item.name == "Renamed"
    assert proxy.password_encrypted == original_cipher
    assert item.has_password is True
    assert item.check_status == ProxyStatus.working  # не сброшен в pending
    assert monitor.checked == []


async def test_update_password_empty_clears_and_rechecks() -> None:
    proxy = FakeProxy(check_status=ProxyStatus.working.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    # password="" → очистить (NULL, has_password=false) + re-check.
    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(password=""))
    await asyncio.sleep(0)

    assert proxy.password_encrypted is None
    assert item.has_password is False
    assert item.check_status == ProxyStatus.pending
    assert item.error_message is None
    assert monitor.checked == [proxy.id]


async def test_update_password_null_clears_and_rechecks() -> None:
    proxy = FakeProxy(check_status=ProxyStatus.error.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    # password=null → очистить + re-check (error → pending).
    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(password=None))
    await asyncio.sleep(0)

    # Явно переданный null отличается от «не передано» по model_fields_set.
    assert "password" in ProxyUpdateRequest(password=None).model_fields_set
    assert proxy.password_encrypted is None
    assert item.has_password is False
    assert item.check_status == ProxyStatus.pending
    assert monitor.checked == [proxy.id]


async def test_update_password_nonempty_reencrypts_and_rechecks() -> None:
    proxy = FakeProxy(check_status=ProxyStatus.working.value)
    old_cipher = proxy.password_encrypted
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(password=NEW_PASSWORD))
    await asyncio.sleep(0)

    # Новый секрет: re-encrypt (шифртекст изменился, round-trip даёт новый пароль).
    assert proxy.password_encrypted is not None
    assert proxy.password_encrypted != old_cipher
    assert decrypt_secret(proxy.password_encrypted) == NEW_PASSWORD
    assert item.has_password is True
    # Re-check: pending, error сброшен, немедленная проверка запущена (prev='pending').
    assert proxy.check_status == ProxyStatus.pending.value
    assert item.check_status == ProxyStatus.pending
    assert monitor.checked == [proxy.id]
    # Пароль отсутствует в ответе.
    assert NEW_PASSWORD not in item.model_dump_json()


# ------------------------------------------------------------------ PATCH: логин
async def test_update_username_value_sets_and_rechecks() -> None:
    proxy = FakeProxy(username="old", check_status=ProxyStatus.working.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(username="new-user"))
    await asyncio.sleep(0)

    assert proxy.username == "new-user"
    assert item.username == "new-user"
    assert item.check_status == ProxyStatus.pending  # username — связанное поле → re-check
    assert monitor.checked == [proxy.id]


async def test_update_username_empty_removes_and_rechecks() -> None:
    proxy = FakeProxy(username="user01", check_status=ProxyStatus.working.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(username=""))
    await asyncio.sleep(0)

    assert proxy.username is None  # "" → убрать логин
    assert item.username is None
    assert item.check_status == ProxyStatus.pending
    assert monitor.checked == [proxy.id]


async def test_update_username_unchanged_value_no_recheck() -> None:
    # username передан, но равен текущему → не считается изменением → без re-check.
    proxy = FakeProxy(username="user01", check_status=ProxyStatus.working.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(username="user01"))
    await asyncio.sleep(0)

    assert item.check_status == ProxyStatus.working
    assert monitor.checked == []


# --------------------------------------------------- PATCH: connection-поля / name
async def test_update_host_change_rechecks() -> None:
    proxy = FakeProxy(host="old.example.com", check_status=ProxyStatus.working.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(host="new.example.com"))
    await asyncio.sleep(0)

    assert proxy.host == "new.example.com"
    assert item.check_status == ProxyStatus.pending
    assert monitor.checked == [proxy.id]


async def test_update_port_change_rechecks() -> None:
    proxy = FakeProxy(port=1080, check_status=ProxyStatus.working.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(port=3128))
    await asyncio.sleep(0)

    assert proxy.port == 3128
    assert item.check_status == ProxyStatus.pending
    assert monitor.checked == [proxy.id]


async def test_update_proxy_type_change_rechecks() -> None:
    proxy = FakeProxy(proxy_type=ProxyType.http.value, check_status=ProxyStatus.working.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(proxy_type=ProxyType.socks5))
    await asyncio.sleep(0)

    assert proxy.proxy_type == ProxyType.socks5.value
    assert item.check_status == ProxyStatus.pending
    assert monitor.checked == [proxy.id]


async def test_update_name_only_no_recheck() -> None:
    proxy = FakeProxy(check_status=ProxyStatus.error.value)
    repo = FakeProxyRepo([proxy])
    monitor = FakeMonitor()
    service = _service(repo, monitor)

    item = await service.update_proxy(proxy.id, ProxyUpdateRequest(name="Only name"))
    await asyncio.sleep(0)

    assert item.name == "Only name"
    assert item.check_status == ProxyStatus.error  # статус сохранён
    assert monitor.checked == []


async def test_update_missing_proxy_raises_404() -> None:
    repo = FakeProxyRepo([FakeProxy()])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.update_proxy(uuid.uuid4(), ProxyUpdateRequest(name="X"))

    assert exc.value.status_code == 404
    assert exc.value.code == "proxy_not_found"


# --------------------------------------------------------------- get_status/delete
async def test_get_status_ok_and_404() -> None:
    proxy = FakeProxy(check_status=ProxyStatus.error.value)
    repo = FakeProxyRepo([proxy])
    service = _service(repo, FakeMonitor())

    status = await service.get_status(proxy.id)
    assert status.check_status == ProxyStatus.error
    assert status.error_message == "Прокси недоступен"

    with pytest.raises(AppError) as exc:
        await service.get_status(uuid.uuid4())
    assert exc.value.code == "proxy_not_found"
    assert exc.value.status_code == 404


async def test_delete_then_repeat_404() -> None:
    proxy = FakeProxy()
    repo = FakeProxyRepo([proxy])
    service = _service(repo, FakeMonitor())

    await service.delete_proxy(proxy.id)  # ok

    with pytest.raises(AppError) as exc:
        await service.delete_proxy(proxy.id)
    assert exc.value.code == "proxy_not_found"
    assert exc.value.status_code == 404


# --------------------------------------------------------------------- reorder
async def test_reorder_nonexistent_id_is_404_before_completeness() -> None:
    p1 = FakeProxy(name="A")
    p2 = FakeProxy(name="B")
    repo = FakeProxyRepo([p1, p2])
    service = _service(repo, FakeMonitor())

    ghost = uuid.uuid4()
    with pytest.raises(AppError) as exc:
        await service.reorder_proxies([p1.id, ghost])

    assert exc.value.status_code == 404
    assert exc.value.code == "proxy_not_found"
    assert repo.reordered is None


async def test_reorder_incomplete_all_existing_is_422() -> None:
    p1 = FakeProxy(name="A")
    p2 = FakeProxy(name="B")
    repo = FakeProxyRepo([p1, p2])
    service = _service(repo, FakeMonitor())

    # Все id существуют, но список неполный (пропущен p2) → 422.
    with pytest.raises(AppError) as exc:
        await service.reorder_proxies([p1.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"
    assert repo.reordered is None


async def test_reorder_duplicate_all_existing_is_422() -> None:
    p1 = FakeProxy(name="A")
    p2 = FakeProxy(name="B")
    repo = FakeProxyRepo([p1, p2])
    service = _service(repo, FakeMonitor())

    with pytest.raises(AppError) as exc:
        await service.reorder_proxies([p1.id, p1.id])

    assert exc.value.status_code == 422
    assert exc.value.code == "unprocessable"


async def test_reorder_full_permutation_assigns_position_0_to_n_minus_1() -> None:
    p1 = FakeProxy(name="A")
    p2 = FakeProxy(name="B")
    p3 = FakeProxy(name="C")
    repo = FakeProxyRepo([p1, p2, p3])
    service = _service(repo, FakeMonitor())

    await service.reorder_proxies([p3.id, p1.id, p2.id])

    assert repo.reordered == [p3.id, p1.id, p2.id]
    assert p3.position == 0
    assert p1.position == 1
    assert p2.position == 2
    assert repo.session.commits == 1
