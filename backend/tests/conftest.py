from __future__ import annotations

import os
import sys
import uuid as _uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
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
    user_id: _uuid.UUID | None = None,
    mail_includes_unassigned: bool = False,
    sms_includes_unassigned: bool = False,
) -> Any:
    """Строит `Principal` (супер-админ по умолчанию — полный каталог прав).

    `user_id` — НЕ-опционален в типе (ADR-051 §1.2): принципала без идентичности больше
    не существует. Умолчание: супер-админ → константа `SUPERADMIN_USER_ID` (строка-якорь,
    ADR-051 §1.1); БД-пользователь → случайный UUID (тестам обычно важен лишь факт
    наличия идентичности, а не конкретное значение — иначе `user_id` передаётся явно).

    `mail_includes_unassigned`/`sms_includes_unassigned` (ADR-055 §2.2/§3) — флаги «Без
    команды» канала (`users.<channel>_includes_unassigned`), уже загруженные в принципал.
    У актора admin-уровня не используются (`sees_all_teams=True` перекрывает их).
    """
    from app.api.deps import Principal
    from app.domain.permissions import full_catalog_permissions
    from app.domain.superadmin import SUPERADMIN_USER_ID

    if user_id is None:
        user_id = SUPERADMIN_USER_ID if is_superadmin else _uuid.uuid4()

    return Principal(
        username=username,
        role=role,
        permissions=full_catalog_permissions() if permissions is None else permissions,
        is_superadmin=is_superadmin,
        user_id=user_id,
        mail_includes_unassigned=mail_includes_unassigned,
        sms_includes_unassigned=sms_includes_unassigned,
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
    `telegram` (опц., ADR-025; заменяет прежний email). `password_hash` nullable
    (`None` = беспарольный пользователь, ADR-025). `teams` — производное свойство
    CRM-команд пользователя (по членству команд в общей `RbacFakeDb`), порядок
    `created_at DESC` (как `User.teams` в модели)."""

    def __init__(
        self,
        *,
        id: _uuid.UUID,
        username: str,
        password_hash: str | None,
        role: Any,
        is_active: bool,
        created_at: datetime,
        updated_at: datetime,
        db: RbacFakeDb,
        telegram: str | None = None,
        first_login_at: datetime | None = None,
        mail_includes_unassigned: bool = False,
        sms_includes_unassigned: bool = False,
    ) -> None:
        self.id = id
        self.username = username
        self.telegram = telegram
        self.password_hash = password_hash
        self._role = role
        self.role_id = role.id if role is not None else None
        self.is_active = is_active
        # Флаги «Без команды» по каналам (ADR-055 §2.2) — колонки
        # `users.mail_includes_unassigned` / `users.sms_includes_unassigned`.
        self.mail_includes_unassigned = mail_includes_unassigned
        self.sms_includes_unassigned = sms_includes_unassigned
        # ADR-028: метка первого успешного входа (NULL = ещё не входил). Источник
        # производного `UserListItem.status`; иначе `_to_item`/`_derive_status` падает.
        self.first_login_at = first_login_at
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
        teams = [t for t in self._db.teams.values() if self.id in t._members]
        return sorted(teams, key=lambda t: t.created_at, reverse=True)


class _FakeTeam:
    """Фейк ORM-команды CRM (опциональный лидер + участники, ADR-022/026).

    Состав хранится как `_members: dict[user_id, created_at]` (дата добавления —
    база порядка авто-передачи лидерства, ADR-026). `leader`/`members` — производные
    из общей `RbacFakeDb` (как `Team.leader`/`Team.members` в модели). `members`
    упорядочены `(created_at ASC, user_id ASC)`. `leader_id` nullable (команда без
    лидера)."""

    def __init__(
        self,
        *,
        id: _uuid.UUID,
        name: str,
        leader_id: _uuid.UUID | None,
        created_at: datetime,
        updated_at: datetime,
        db: RbacFakeDb,
        members: dict[_uuid.UUID, datetime],
        mail_group_id: int | None = None,
    ) -> None:
        self.id = id
        self.name = name
        # Привязка к группе mail-агрегатора (ADR-038); None — без привязки.
        self.mail_group_id = mail_group_id
        self.leader_id = leader_id
        self.created_at = created_at
        self.updated_at = updated_at
        self._db = db
        self._members = dict(members)

    @property
    def leader(self) -> Any:
        if self.leader_id is None:
            return None
        return self._db.users.get(self.leader_id)

    @property
    def members(self) -> list[Any]:
        present = [(uid, ts) for uid, ts in self._members.items() if uid in self._db.users]
        ordered = sorted(present, key=lambda item: (item[1], str(item[0])))
        return [self._db.users[uid] for uid, _ in ordered]


class _FakeSession:
    """Фейк AsyncSession: commit/rollback/refresh/flush — no-op (in-memory фейки репо).

    `raise_integrity=True` заставляет `commit()` бросить IntegrityError — для проверки
    race-ветки сервисов (детерминированный 409 при гонке уникальности)."""

    def __init__(self) -> None:
        self.raise_integrity = False
        # Имя нарушенного констрейнта для structured-ветки различения гонки
        # name↔mail_group (ADR-038). None → драйвер имени не отдал (фолбэк-перепроверка).
        self.integrity_constraint: str | None = None

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        """No-op для SQL-хуков поверх фейк-репозиториев (ADR-044 §6: синхронный
        orphan-резолв `bind_orphans_for_user` в UserService дергает `session.execute`).

        In-memory фейки не держат `mail_telegram_links`, orphan'ов нет → возвращаем
        результат с `rowcount=0` (хук ничего не связывает). Orphan-связывание покрыто
        отдельно на РЕАЛЬНОМ Postgres (`tests/integration/test_mail_orphan_resolve.py`).

        `scalars().all()` → `[]`: `GET /api/auth/me` под актором admin-уровня зовёт
        `TeamRepository.list_refs()` (ADR-055 §5.1 — ему отдаются ВСЕ команды системы)
        прямо на сессии; в тестах без Postgres это «команд в системе нет» → `[]`."""
        return SimpleNamespace(
            rowcount=0,
            scalars=lambda: SimpleNamespace(all=lambda: []),
            all=lambda: [],
        )

    async def commit(self) -> None:
        if self.raise_integrity:
            orig = Exception("duplicate")
            if self.integrity_constraint is not None:
                orig.constraint_name = self.integrity_constraint  # type: ignore[attr-defined]
            raise IntegrityError("stmt", {}, orig)

    async def rollback(self) -> None:
        return None

    async def refresh(self, _obj: Any) -> None:
        return None

    async def flush(self) -> None:
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

    async def get_by_telegram(self, telegram: str) -> Any | None:
        if not telegram:
            return None
        return next((u for u in self._db.users.values() if u.telegram == telegram), None)

    async def exists_by_username(
        self, username: str, *, exclude_id: _uuid.UUID | None = None
    ) -> bool:
        return any(u.username == username and u.id != exclude_id for u in self._db.users.values())

    async def exists_by_telegram(
        self, telegram: str, *, exclude_id: _uuid.UUID | None = None
    ) -> bool:
        return any(u.telegram == telegram and u.id != exclude_id for u in self._db.users.values())

    async def get_existing_ids(self, ids: set[_uuid.UUID]) -> set[_uuid.UUID]:
        return {uid for uid in ids if uid in self._db.users}

    async def get_with_teams(self, user_id: _uuid.UUID) -> Any | None:
        return self._db.users.get(user_id)

    async def team_ids_of_user(self, user_id: _uuid.UUID) -> set[_uuid.UUID]:
        return {t.id for t in self._db.teams.values() if user_id in t._members}

    async def set_membership(self, user_id: _uuid.UUID, team_ids: set[_uuid.UUID]) -> None:
        # Существующие членства СОХРАНЯЮТ created_at (дата добавления, ADR-026);
        # удаляются только выбывшие, добавляются только новые (с новым created_at).
        for team in self._db.teams.values():
            if team.id not in team_ids and user_id in team._members:
                del team._members[user_id]
        for tid in team_ids:
            team = self._db.teams.get(tid)
            if team is not None and user_id not in team._members:
                team._members[user_id] = self._db.next_created_at()

    async def create(
        self,
        *,
        username: str,
        telegram: str | None = None,
        password_hash: str | None,
        role_id: _uuid.UUID,
    ) -> Any:
        role = self._db.roles.get(role_id)
        now = datetime.now(UTC)
        user = _FakeUser(
            id=_uuid.uuid4(),
            username=username,
            telegram=telegram,
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
        # Каскад user_teams: снять членства удаляемого пользователя.
        for team in self._db.teams.values():
            team._members.pop(user_id, None)
        # Каскад user_channel_teams (FK ON DELETE CASCADE, ADR-055 §2.1).
        for key in [k for k in self._db.channel_extras if k[0] == user_id]:
            del self._db.channel_extras[key]
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
    """In-memory замена TeamRepository (тот же интерфейс, без БД, ADR-022/026)."""

    def __init__(self, db: RbacFakeDb) -> None:
        self._db = db

    @property
    def session(self) -> _FakeSession:
        return self._db.session

    async def list_all(self) -> list[Any]:
        teams = sorted(self._db.teams.values(), key=lambda t: str(t.id))
        return sorted(teams, key=lambda t: t.created_at, reverse=True)

    async def get_with_members(self, team_id: _uuid.UUID) -> Any | None:
        return self._db.teams.get(team_id)

    async def get(self, team_id: _uuid.UUID) -> Any | None:
        return self._db.teams.get(team_id)

    async def exists_by_name(self, name: str, *, exclude_id: _uuid.UUID | None = None) -> bool:
        return any(t.name == name and t.id != exclude_id for t in self._db.teams.values())

    async def exists_by_mail_group_id(
        self, mail_group_id: int, *, exclude_id: _uuid.UUID | None = None
    ) -> bool:
        return any(
            t.mail_group_id == mail_group_id and t.id != exclude_id for t in self._db.teams.values()
        )

    async def get_existing_ids(self, ids: set[_uuid.UUID]) -> set[_uuid.UUID]:
        return {tid for tid in ids if tid in self._db.teams}

    async def ids_led_by(self, user_id: _uuid.UUID) -> set[_uuid.UUID]:
        return {t.id for t in self._db.teams.values() if t.leader_id == user_id}

    async def create(
        self,
        *,
        name: str,
        leader_id: _uuid.UUID | None,
        ordered_member_ids: list[_uuid.UUID],
        mail_group_id: int | None = None,
    ) -> Any:
        now = datetime.now(UTC)
        members: dict[_uuid.UUID, datetime] = {}
        for uid in dict.fromkeys(ordered_member_ids):
            members[uid] = self._db.next_created_at()
        team = _FakeTeam(
            id=_uuid.uuid4(),
            name=name,
            leader_id=leader_id,
            created_at=now,
            updated_at=now,
            db=self._db,
            members=members,
            mail_group_id=mail_group_id,
        )
        self._db.teams[team.id] = team
        return team

    async def replace_members(
        self, team_id: _uuid.UUID, ordered_member_ids: list[_uuid.UUID]
    ) -> None:
        team = self._db.teams[team_id]
        desired = list(dict.fromkeys(ordered_member_ids))
        desired_set = set(desired)
        # Выбывшие — удалить; остающиеся — сохранить created_at; новые — позже макс.
        for uid in list(team._members):
            if uid not in desired_set:
                del team._members[uid]
        for uid in desired:
            if uid not in team._members:
                team._members[uid] = self._db.next_created_at()

    async def get_first_member(self, team_id: _uuid.UUID) -> _uuid.UUID | None:
        team = self._db.teams.get(team_id)
        if team is None or not team._members:
            return None
        ordered = sorted(team._members.items(), key=lambda item: (item[1], str(item[0])))
        return ordered[0][0]

    async def promote_next_leader(
        self, team_id: _uuid.UUID, *, exclude_user_id: _uuid.UUID
    ) -> _uuid.UUID | None:
        team = self._db.teams.get(team_id)
        if team is None:
            return None
        candidates = [(uid, ts) for uid, ts in team._members.items() if uid != exclude_user_id]
        next_leader: _uuid.UUID | None = None
        if candidates:
            next_leader = sorted(candidates, key=lambda item: (item[1], str(item[0])))[0][0]
        team.leader_id = next_leader
        return next_leader

    async def delete_by_id(self, team_id: _uuid.UUID) -> bool:
        # Каскад user_channel_teams (FK ON DELETE CASCADE, ADR-055 §2.1).
        for key, ids in list(self._db.channel_extras.items()):
            ids.discard(team_id)
            if not ids:
                del self._db.channel_extras[key]
        return self._db.teams.pop(team_id, None) is not None


class _FakeSmsNumber:
    """Фейк ORM SMS-номера (для number_count/list_by_team в TeamService, ADR-030).

    `team` — производное свойство текущей команды (по `team_id` из общей `RbacFakeDb`),
    как relationship `SmsPhoneNumber.team`. Присваивание `.team_id` меняет владельца."""

    def __init__(
        self,
        *,
        id: int,
        phone_number: str,
        team_id: _uuid.UUID | None,
        label: str | None,
        login: str | None,
        app_name: str | None,
        note: str | None,
        is_active: bool,
        created_at: datetime,
        updated_at: datetime,
        db: RbacFakeDb,
    ) -> None:
        self.id = id
        self.phone_number = phone_number
        self.team_id = team_id
        self.label = label
        self.login = login
        self.app_name = app_name
        self.note = note
        self.is_active = is_active
        self.created_at = created_at
        self.updated_at = updated_at
        self._db = db

    @property
    def team(self) -> Any:
        if self.team_id is None:
            return None
        return self._db.teams.get(self.team_id)


class _FakeSmsNumberRepo:
    """In-memory замена SmsNumberRepository (подмножество, нужное TeamService, ADR-030)."""

    def __init__(self, db: RbacFakeDb) -> None:
        self._db = db

    @property
    def session(self) -> _FakeSession:
        return self._db.session

    async def count_by_teams(self, team_ids: Any) -> dict[_uuid.UUID, int]:
        ids = set(team_ids)
        counts: dict[_uuid.UUID, int] = {}
        for number in self._db.numbers.values():
            if number.team_id in ids:
                counts[number.team_id] = counts.get(number.team_id, 0) + 1
        return counts

    async def count_by_team(self, team_id: _uuid.UUID) -> int:
        return sum(1 for n in self._db.numbers.values() if n.team_id == team_id)

    async def list_by_team(self, team_id: _uuid.UUID) -> list[Any]:
        rows = [n for n in self._db.numbers.values() if n.team_id == team_id]
        return sorted(rows, key=lambda n: (n.created_at, n.id), reverse=True)


class _FakeMailAccount:
    """Фейк ORM почтового ящика (для mailbox_count в TeamService, ADR-048 §1).

    Достаточно полей, которые читает агрегат: `id`/`team_id`. Креды/хосты/статус синка
    в фейке не нужны — `TeamService` их не касается (сужение ADR-044 §4)."""

    def __init__(
        self,
        *,
        id: int,
        email: str,
        team_id: _uuid.UUID | None,
        number: str | None,
        app_name: str | None,
        display_name: str | None,
        is_active: bool,
    ) -> None:
        self.id = id
        self.email = email
        self.team_id = team_id
        self.number = number
        self.app_name = app_name
        self.display_name = display_name
        self.is_active = is_active


class _FakeMailAccountRepo:
    """In-memory замена MailAccountRepository (подмножество для TeamService, ADR-048 §1).

    Симметрична `_FakeSmsNumberRepo`: батч-агрегат `count_by_teams` (список команд) +
    одиночный `count_by_team` (тела 201/200), плюс `list_by_team` (секция «Почты команды»)."""

    def __init__(self, db: RbacFakeDb) -> None:
        self._db = db

    @property
    def session(self) -> _FakeSession:
        return self._db.session

    async def count_by_teams(self, team_ids: Any) -> dict[_uuid.UUID, int]:
        ids = set(team_ids)
        counts: dict[_uuid.UUID, int] = {}
        for mailbox in self._db.mailboxes.values():
            if mailbox.team_id in ids:
                counts[mailbox.team_id] = counts.get(mailbox.team_id, 0) + 1
        return counts

    async def count_by_team(self, team_id: _uuid.UUID) -> int:
        return sum(1 for mb in self._db.mailboxes.values() if mb.team_id == team_id)

    async def list_by_team(self, team_id: _uuid.UUID) -> list[Any]:
        rows = [mb for mb in self._db.mailboxes.values() if mb.team_id == team_id]
        return sorted(rows, key=lambda mb: (mb.email, mb.id))


class _FakeUserChannelTeamRepo:
    """In-memory замена `UserChannelTeamRepository` (ADR-055 §2/§3/§5.2).

    Хранилище — `RbacFakeDb.channel_extras: {(user_id, channel): {team_id, ...}}`, то есть
    **только добавка** (базовые команды `user_teams` сюда не пишутся — инвариант §2.3
    обеспечивают сервисы users/teams, ровно как в реальном репозитории). `scope_team_ids` /
    `effective_teams` возвращают ЭФФЕКТИВНЫЙ набор (базовые ∪ добавка) — как UNION-запрос.
    """

    def __init__(self, db: RbacFakeDb) -> None:
        self._db = db

    @property
    def session(self) -> _FakeSession:
        return self._db.session

    def _base_team_ids(self, user_id: _uuid.UUID) -> set[_uuid.UUID]:
        return {t.id for t in self._db.teams.values() if user_id in t._members}

    async def scope_team_ids(self, user_id: _uuid.UUID, channel: str) -> frozenset[_uuid.UUID]:
        extra = self._db.channel_extras.get((user_id, channel), set())
        return frozenset(self._base_team_ids(user_id) | extra)

    async def effective_teams(self, user_id: _uuid.UUID, channel: str) -> list[Any]:
        ids = await self.scope_team_ids(user_id, channel)
        return [t for t in self._db.teams.values() if t.id in ids]

    async def extra_team_ids(self, user_id: _uuid.UUID, channel: str) -> set[_uuid.UUID]:
        return set(self._db.channel_extras.get((user_id, channel), set()))

    async def extras_for_users(self, user_ids: Any) -> dict[tuple[_uuid.UUID, str], list[Any]]:
        ids = set(user_ids)
        extras: dict[tuple[_uuid.UUID, str], list[Any]] = {}
        for (user_id, channel), team_ids in self._db.channel_extras.items():
            if user_id not in ids:
                continue
            teams = [self._db.teams[tid] for tid in team_ids if tid in self._db.teams]
            if teams:
                extras[(user_id, channel)] = teams
        return extras

    async def replace_extras(
        self, user_id: _uuid.UUID, channel: str, team_ids: set[_uuid.UUID]
    ) -> None:
        if team_ids:
            self._db.channel_extras[(user_id, channel)] = set(team_ids)
        else:
            self._db.channel_extras.pop((user_id, channel), None)

    async def remove_team_for_users(self, team_id: _uuid.UUID, user_ids: Any) -> None:
        for user_id in set(user_ids):
            for channel in ("mail", "sms"):
                current = self._db.channel_extras.get((user_id, channel))
                if current is None:
                    continue
                current.discard(team_id)
                if not current:
                    del self._db.channel_extras[(user_id, channel)]


class RbacFakeDb:
    """In-memory «БД» для user/role/team репозиториев (общее состояние + сессия).

    Позволяет прогонять реальные `UserService`/`RoleService`/`TeamService` без Postgres,
    сохраняя установленную в репо конвенцию тестов (фейки границ, offline-SQL для миграций)."""

    def __init__(self) -> None:
        self.session = _FakeSession()
        self.roles: dict[_uuid.UUID, Any] = {}
        self.users: dict[_uuid.UUID, Any] = {}
        self.teams: dict[_uuid.UUID, Any] = {}
        self.numbers: dict[int, Any] = {}
        self.mailboxes: dict[int, Any] = {}
        # `user_channel_teams` (ADR-055 §2.1): ТОЛЬКО добавка канала, базовые команды
        # (`user_teams`) сюда не пишутся — инвариант нормализации §2.3.
        self.channel_extras: dict[tuple[_uuid.UUID, str], set[_uuid.UUID]] = {}
        self.user_repo = _FakeUserRepo(self)
        self.role_repo = _FakeRoleRepo(self)
        self.team_repo = _FakeTeamRepo(self)
        self.number_repo = _FakeSmsNumberRepo(self)
        self.mailbox_repo = _FakeMailAccountRepo(self)
        self.channel_repo = _FakeUserChannelTeamRepo(self)
        # Монотонный источник `user_teams.created_at` (детерминированный порядок
        # авто-передачи лидерства без зависимости от разрешения системного таймера).
        self._member_seq = 0
        self._number_seq = 0
        self._mailbox_seq = 0

    def next_created_at(self) -> datetime:
        """Строго возрастающая «дата добавления» участника (детерминизм ADR-026)."""
        self._member_seq += 1
        return datetime(2020, 1, 1, tzinfo=UTC) + timedelta(microseconds=self._member_seq)

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
        password_hash: str | None = "x",
        telegram: str | None = None,
        first_login_at: datetime | None = None,
        mail_includes_unassigned: bool = False,
        sms_includes_unassigned: bool = False,
    ) -> Any:
        now = datetime.now(UTC)
        user = _FakeUser(
            id=_uuid.uuid4(),
            username=username,
            telegram=telegram,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            created_at=now,
            updated_at=now,
            db=self,
            first_login_at=first_login_at,
            mail_includes_unassigned=mail_includes_unassigned,
            sms_includes_unassigned=sms_includes_unassigned,
        )
        self.users[user.id] = user
        return user

    def add_extra_team(self, user: Any, channel: str, team: Any) -> None:
        """Строка `user_channel_teams (user, channel, team)` — доп-команда канала (§2.1).

        Прямая запись в хранилище (минуя сервис) — так тест воспроизводит состояние БД,
        в т.ч. «висящую» добавку, которую обязан снять инвариант нормализации §2.3.
        """
        self.channel_extras.setdefault((user.id, channel), set()).add(team.id)

    def add_team(
        self,
        name: str,
        leader: Any = None,
        *,
        members: list[Any] | None = None,
        mail_group_id: int | None = None,
    ) -> Any:
        """Создаёт CRM-команду; заданный лидер всегда в участниках (инвариант, ADR-026).

        `leader=None` → команда без лидера. Порядок добавления участников: сначала
        `members` (в переданном порядке), затем лидер (если ещё не в составе).
        `mail_group_id` — привязка к группе mail-агрегатора (ADR-038)."""
        now = datetime.now(UTC)
        ordered: list[Any] = list(members or [])
        if leader is not None and leader not in ordered:
            ordered.append(leader)
        member_map: dict[_uuid.UUID, datetime] = {}
        for m in ordered:
            if m.id not in member_map:
                member_map[m.id] = self.next_created_at()
        team = _FakeTeam(
            id=_uuid.uuid4(),
            name=name,
            leader_id=leader.id if leader is not None else None,
            created_at=now,
            updated_at=now,
            db=self,
            members=member_map,
            mail_group_id=mail_group_id,
        )
        self.teams[team.id] = team
        return team

    def add_number(
        self,
        phone_number: str,
        *,
        team: Any = None,
        label: str | None = None,
        login: str | None = None,
        app_name: str | None = None,
        note: str | None = None,
        is_active: bool = True,
    ) -> Any:
        """Создаёт SMS-номер (для number_count/list_team_numbers в TeamService, ADR-030)."""
        self._number_seq += 1
        now = datetime.now(UTC)
        number = _FakeSmsNumber(
            id=self._number_seq,
            phone_number=phone_number,
            team_id=team.id if team is not None else None,
            label=label,
            login=login,
            app_name=app_name,
            note=note,
            is_active=is_active,
            created_at=now,
            updated_at=now,
            db=self,
        )
        self.numbers[number.id] = number
        return number

    def add_mailbox(
        self,
        email: str,
        *,
        team: Any = None,
        number: str | None = None,
        app_name: str | None = None,
        display_name: str | None = None,
        is_active: bool = True,
    ) -> Any:
        """Создаёт почтовый ящик каталога (для mailbox_count в TeamService, ADR-048 §1)."""
        self._mailbox_seq += 1
        mailbox = _FakeMailAccount(
            id=self._mailbox_seq,
            email=email,
            team_id=team.id if team is not None else None,
            number=number,
            app_name=app_name,
            display_name=display_name,
            is_active=is_active,
        )
        self.mailboxes[mailbox.id] = mailbox
        return mailbox


@pytest.fixture
def rbac_db() -> RbacFakeDb:
    """Общий in-memory фейк user/role-репозиториев для сервис/контракт-тестов RBAC."""
    return RbacFakeDb()
