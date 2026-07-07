from __future__ import annotations

import os
import sys
import uuid as _uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def reset_structlog() -> Iterator[None]:
    """Изолирует глобальное состояние structlog между тестами.

    Старт приложения и интеграционные тесты вызывают `configure_logging`
    (`cache_logger_on_first_use=True`), что кеширует bound-логгеры на устаревший
    список процессоров. Из-за этого `structlog.testing.capture_logs()` в другом тесте
    может не перехватывать события (ordering-зависимость). Сброс к дефолтам до и после
    каждого теста устраняет утечку глобального конфига structlog.
    """
    structlog.reset_defaults()
    yield
    structlog.reset_defaults()


@pytest.fixture(autouse=True)
def test_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret")
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-more-than-32-bytes")
    monkeypatch.setenv("JWT_EXPIRES_MIN", "1440")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SEC", "300")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
    monkeypatch.setenv("FILE_SD_DIR", os.path.join(os.getcwd(), ".pytest-file-sd"))

    from app.config import get_settings
    from app.infra import rate_limit

    get_settings.cache_clear()
    rate_limit._limiter = None
    yield
    get_settings.cache_clear()
    rate_limit._limiter = None


# --- Общие RBAC-хелперы (ADR-021): принципалы и in-memory фейки репозиториев ---
#
# Ресурсные роутеры больше НЕ гейтятся `get_current_user` (удалён): они защищены
# фабрикой `require(page, action)` поверх `get_current_principal` (ADR-021). Общий
# хелпер `override_principal` ставит override `get_current_principal` для интеграционных
# тестов (супер-админ по умолчанию → проходит любой `require(...)`/`require_admin`).


def make_principal(
    *,
    username: str = "admin",
    role: str = "admin",
    permissions: dict[str, list[str]] | None = None,
    is_superadmin: bool = True,
) -> Any:
    """Строит `Principal` (супер-админ по умолчанию — полный каталог прав)."""
    from app.api.deps import Principal
    from app.domain.permissions import full_catalog_permissions

    return Principal(
        username=username,
        role=role,
        permissions=full_catalog_permissions() if permissions is None else permissions,
        is_superadmin=is_superadmin,
    )


@pytest.fixture
def override_principal() -> Callable[..., Any]:
    """Возвращает функцию, ставящую override `get_current_principal` на app.

    Общий хелпер для интеграционных тестов ресурсных роутеров (замена прежнего
    override `get_current_user`). По умолчанию — супер-админ (полный доступ).
    """
    from app.api import deps

    def _apply(app: Any, **kwargs: Any) -> Any:
        principal = make_principal(**kwargs)
        app.dependency_overrides[deps.get_current_principal] = lambda: principal
        return principal

    return _apply


class _FakeUser:
    """Фейк ORM-пользователя: присваивание `.role` синхронизирует `.role_id`.

    Модель SQLAlchemy при `user.role = role` обновляет FK `role_id` на flush; фейк
    воспроизводит это через property-сеттер, чтобы сервис-тесты видели то же поведение."""

    def __init__(
        self,
        *,
        id: _uuid.UUID,
        username: str,
        password_hash: str,
        role: Any,
        is_active: bool,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self._role = role
        self.role_id = role.id if role is not None else None
        self.is_active = is_active
        self.created_at = created_at
        self.updated_at = updated_at

    @property
    def role(self) -> Any:
        return self._role

    @role.setter
    def role(self, value: Any) -> None:
        self._role = value
        self.role_id = value.id if value is not None else None


class _FakeSession:
    """Фейк AsyncSession: commit/rollback/refresh — no-op (in-memory фейки репо).

    `raise_integrity=True` заставляет `commit()` бросить IntegrityError — для проверки
    race-ветки сервисов (детерминированный 409 при гонке уникальности)."""

    def __init__(self) -> None:
        self.raise_integrity = False

    async def commit(self) -> None:
        if self.raise_integrity:
            raise IntegrityError("stmt", {}, Exception("duplicate"))

    async def rollback(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None


class _FakeUserRepo:
    """In-memory замена UserRepository (тот же интерфейс, без БД)."""

    def __init__(self, db: RbacFakeDb) -> None:
        self._db = db

    @property
    def session(self) -> _FakeSession:
        return self._db.session

    async def list_all(self) -> list[Any]:
        return sorted(self._db.users.values(), key=lambda u: (u.created_at, str(u.id)))

    async def get_by_id(self, user_id: _uuid.UUID) -> Any | None:
        return self._db.users.get(user_id)

    async def get_by_username(self, username: str) -> Any | None:
        return next((u for u in self._db.users.values() if u.username == username), None)

    async def exists_by_username(
        self, username: str, *, exclude_id: _uuid.UUID | None = None
    ) -> bool:
        return any(u.username == username and u.id != exclude_id for u in self._db.users.values())

    async def create(self, *, username: str, password_hash: str, role_id: _uuid.UUID) -> Any:
        role = self._db.roles.get(role_id)
        now = datetime.now(UTC)
        user = _FakeUser(
            id=_uuid.uuid4(),
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        self._db.users[user.id] = user
        return user

    async def delete_by_id(self, user_id: _uuid.UUID) -> bool:
        return self._db.users.pop(user_id, None) is not None


class _FakeRoleRepo:
    """In-memory замена RoleRepository (тот же интерфейс, без БД)."""

    def __init__(self, db: RbacFakeDb) -> None:
        self._db = db

    @property
    def session(self) -> _FakeSession:
        return self._db.session

    async def list_all(self) -> list[Any]:
        return sorted(self._db.roles.values(), key=lambda r: (r.created_at, str(r.id)))

    async def get_by_id(self, role_id: _uuid.UUID) -> Any | None:
        return self._db.roles.get(role_id)

    async def exists_by_name(self, name: str, *, exclude_id: _uuid.UUID | None = None) -> bool:
        return any(r.name == name and r.id != exclude_id for r in self._db.roles.values())

    async def is_in_use(self, role_id: _uuid.UUID) -> bool:
        return any(u.role_id == role_id for u in self._db.users.values())

    async def create(self, *, name: str, permissions: dict[str, list[str]]) -> Any:
        now = datetime.now(UTC)
        role = SimpleNamespace(
            id=_uuid.uuid4(),
            name=name,
            permissions=permissions,
            created_at=now,
            updated_at=now,
        )
        self._db.roles[role.id] = role
        return role

    async def delete_by_id(self, role_id: _uuid.UUID) -> bool:
        return self._db.roles.pop(role_id, None) is not None


class RbacFakeDb:
    """In-memory «БД» для user/role репозиториев (общее состояние + сессия).

    Позволяет прогонять реальные `UserService`/`RoleService` без Postgres, сохраняя
    установленную в репо конвенцию тестов (фейки границ, offline-SQL для миграций)."""

    def __init__(self) -> None:
        self.session = _FakeSession()
        self.roles: dict[_uuid.UUID, Any] = {}
        self.users: dict[_uuid.UUID, Any] = {}
        self.user_repo = _FakeUserRepo(self)
        self.role_repo = _FakeRoleRepo(self)

    def add_role(self, name: str, permissions: dict[str, list[str]]) -> Any:
        now = datetime.now(UTC)
        role = SimpleNamespace(
            id=_uuid.uuid4(), name=name, permissions=permissions, created_at=now, updated_at=now
        )
        self.roles[role.id] = role
        return role

    def add_user(
        self, username: str, role: Any, *, is_active: bool = True, password_hash: str = "x"
    ) -> Any:
        now = datetime.now(UTC)
        user = _FakeUser(
            id=_uuid.uuid4(),
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )
        self.users[user.id] = user
        return user


@pytest.fixture
def rbac_db() -> RbacFakeDb:
    """Общий in-memory фейк user/role-репозиториев для сервис/контракт-тестов RBAC."""
    return RbacFakeDb()
