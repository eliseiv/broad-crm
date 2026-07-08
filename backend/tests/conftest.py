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
    воспроизводит это через property-сеттер, чтобы сервис-тесты видели то же поведение.
    `email` — опциональный (ADR-022). `teams` — производное свойство CRM-команд
    пользователя (по членству `_member_ids` команд в общей `RbacFakeDb`), порядок
    `created_at DESC` (как `User.teams` в модели)."""

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
        db: RbacFakeDb,
        email: str | None = None,
    ) -> None:
        self.id = id
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self._role = role
        self.role_id = role.id if role is not None else None
        self.is_active = is_active
        self.created_at = created_at
        self.updated_at = updated_at
        self._db = db

    @property
    def role(self) -> Any:
        return self._role

    @role.setter
    def role(self, value: Any) -> None:
        self._role = value
        self.role_id = value.id if value is not None else None

    @property
    def teams(self) -> list[Any]:
        teams = [t for t in self._db.teams.values() if self.id in t._member_ids]
        return sorted(teams, key=lambda t: t.created_at, reverse=True)


class _FakeTeam:
    """Фейк ORM-команды CRM (лидер + участники, ADR-022).

    Состав хранится как `_member_ids` (включая лидера — инвариант обеспечивает сервис).
    `leader`/`members` — производные из общей `RbacFakeDb` (как `Team.leader`/`Team.members`
    в модели). `members` упорядочены `created_at ASC`."""

    def __init__(
        self,
        *,
        id: _uuid.UUID,
        name: str,
        leader_id: _uuid.UUID,
        created_at: datetime,
        updated_at: datetime,
        db: RbacFakeDb,
        member_ids: set[_uuid.UUID],
    ) -> None:
        self.id = id
        self.name = name
        self.leader_id = leader_id
        self.created_at = created_at
        self.updated_at = updated_at
        self._db = db
        self._member_ids = set(member_ids)

    @property
    def leader(self) -> Any:
        return self._db.users.get(self.leader_id)

    @property
    def members(self) -> list[Any]:
        members = [self._db.users[uid] for uid in self._member_ids if uid in self._db.users]
        return sorted(members, key=lambda u: u.created_at)


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

    async def exists_by_email(self, email: str, *, exclude_id: _uuid.UUID | None = None) -> bool:
        return any(u.email == email and u.id != exclude_id for u in self._db.users.values())

    async def get_existing_ids(self, ids: set[_uuid.UUID]) -> set[_uuid.UUID]:
        return {uid for uid in ids if uid in self._db.users}

    async def get_with_teams(self, user_id: _uuid.UUID) -> Any | None:
        return self._db.users.get(user_id)

    async def set_membership(self, user_id: _uuid.UUID, team_ids: set[_uuid.UUID]) -> None:
        for team in self._db.teams.values():
            team._member_ids.discard(user_id)
        for tid in team_ids:
            team = self._db.teams.get(tid)
            if team is not None:
                team._member_ids.add(user_id)

    async def create(
        self,
        *,
        username: str,
        email: str | None = None,
        password_hash: str,
        role_id: _uuid.UUID,
    ) -> Any:
        role = self._db.roles.get(role_id)
        now = datetime.now(UTC)
        user = _FakeUser(
            id=_uuid.uuid4(),
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
            db=self._db,
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

    async def list_all_with_counts(self) -> list[tuple[Any, int]]:
        roles = sorted(self._db.roles.values(), key=lambda r: (r.created_at, str(r.id)))
        return [(role, await self.count_users(role.id)) for role in roles]

    async def count_users(self, role_id: _uuid.UUID) -> int:
        return sum(1 for u in self._db.users.values() if u.role_id == role_id)

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


class _FakeTeamRepo:
    """In-memory замена TeamRepository (тот же интерфейс, без БД, ADR-022)."""

    def __init__(self, db: RbacFakeDb) -> None:
        self._db = db

    @property
    def session(self) -> _FakeSession:
        return self._db.session

    async def list_all(self) -> list[Any]:
        # created_at DESC, id ASC (стабильная сортировка: сначала id ASC, затем ts DESC).
        teams = sorted(self._db.teams.values(), key=lambda t: str(t.id))
        return sorted(teams, key=lambda t: t.created_at, reverse=True)

    async def get_with_members(self, team_id: _uuid.UUID) -> Any | None:
        return self._db.teams.get(team_id)

    async def get(self, team_id: _uuid.UUID) -> Any | None:
        return self._db.teams.get(team_id)

    async def exists_by_name(self, name: str, *, exclude_id: _uuid.UUID | None = None) -> bool:
        return any(t.name == name and t.id != exclude_id for t in self._db.teams.values())

    async def get_existing_ids(self, ids: set[_uuid.UUID]) -> set[_uuid.UUID]:
        return {tid for tid in ids if tid in self._db.teams}

    async def ids_led_by(self, user_id: _uuid.UUID) -> set[_uuid.UUID]:
        return {t.id for t in self._db.teams.values() if t.leader_id == user_id}

    async def create(self, *, name: str, leader_id: _uuid.UUID, member_ids: set[_uuid.UUID]) -> Any:
        now = datetime.now(UTC)
        team = _FakeTeam(
            id=_uuid.uuid4(),
            name=name,
            leader_id=leader_id,
            created_at=now,
            updated_at=now,
            db=self._db,
            member_ids=set(member_ids) | {leader_id},
        )
        self._db.teams[team.id] = team
        return team

    async def replace_members(self, team_id: _uuid.UUID, member_ids: set[_uuid.UUID]) -> None:
        self._db.teams[team_id]._member_ids = set(member_ids)

    async def delete_by_id(self, team_id: _uuid.UUID) -> bool:
        return self._db.teams.pop(team_id, None) is not None


class RbacFakeDb:
    """In-memory «БД» для user/role/team репозиториев (общее состояние + сессия).

    Позволяет прогонять реальные `UserService`/`RoleService`/`TeamService` без Postgres,
    сохраняя установленную в репо конвенцию тестов (фейки границ, offline-SQL для миграций)."""

    def __init__(self) -> None:
        self.session = _FakeSession()
        self.roles: dict[_uuid.UUID, Any] = {}
        self.users: dict[_uuid.UUID, Any] = {}
        self.teams: dict[_uuid.UUID, Any] = {}
        self.user_repo = _FakeUserRepo(self)
        self.role_repo = _FakeRoleRepo(self)
        self.team_repo = _FakeTeamRepo(self)

    def add_role(self, name: str, permissions: dict[str, list[str]]) -> Any:
        now = datetime.now(UTC)
        role = SimpleNamespace(
            id=_uuid.uuid4(), name=name, permissions=permissions, created_at=now, updated_at=now
        )
        self.roles[role.id] = role
        return role

    def add_user(
        self,
        username: str,
        role: Any,
        *,
        is_active: bool = True,
        password_hash: str = "x",
        email: str | None = None,
    ) -> Any:
        now = datetime.now(UTC)
        user = _FakeUser(
            id=_uuid.uuid4(),
            username=username,
            email=email,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            created_at=now,
            updated_at=now,
            db=self,
        )
        self.users[user.id] = user
        return user

    def add_team(self, name: str, leader: Any, *, members: list[Any] | None = None) -> Any:
        """Создаёт CRM-команду; лидер всегда в участниках (инвариант)."""
        now = datetime.now(UTC)
        member_ids = {m.id for m in (members or [])} | {leader.id}
        team = _FakeTeam(
            id=_uuid.uuid4(),
            name=name,
            leader_id=leader.id,
            created_at=now,
            updated_at=now,
            db=self,
            member_ids=member_ids,
        )
        self.teams[team.id] = team
        return team


@pytest.fixture
def rbac_db() -> RbacFakeDb:
    """Общий in-memory фейк user/role-репозиториев для сервис/контракт-тестов RBAC."""
    return RbacFakeDb()
