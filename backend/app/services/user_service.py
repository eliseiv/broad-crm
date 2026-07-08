"""Бизнес-логика реестра пользователей (modules/auth, 04-api.md#users, ADR-021/022).

Пароль хранится только как bcrypt-хэш; plaintext не возвращается/не логируется.
`email` (опц.) и членство в CRM-командах (`team_ids`) — ADR-022.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.email import EmailFormatError, validate_email
from app.domain.identity import IdentityNameError, validate_identity_name
from app.errors import (
    email_taken,
    unprocessable,
    user_is_team_leader,
    user_not_found,
    username_taken,
)
from app.infra.passwords import hash_password
from app.logging import get_logger
from app.models.user import User
from app.repositories.role_repository import RoleRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    TeamRef,
    UserCreateRequest,
    UserListItem,
    UserListResponse,
    UserUpdateRequest,
)

logger = get_logger(__name__)

# Политика пароля БД-пользователя (05-security.md): 8–128 символов.
_PASSWORD_MIN_LEN = 8
_PASSWORD_MAX_LEN = 128


def _validate_username(raw: str) -> str:
    """Валидирует/нормализует username; нарушение → 422 unprocessable."""
    try:
        return validate_identity_name(raw)
    except IdentityNameError as exc:
        raise unprocessable(
            "Недопустимое имя пользователя",
            details=[{"field": "username", "message": str(exc)}],
        ) from exc


def _validate_email(raw: str) -> str:
    """Валидирует/нормализует email; нарушение → 422 unprocessable."""
    try:
        return validate_email(raw)
    except EmailFormatError as exc:
        raise unprocessable(
            "Недопустимый email",
            details=[{"field": "email", "message": str(exc)}],
        ) from exc


def _validate_password_reset(password: str) -> None:
    """Проверяет длину пароля при сбросе (PATCH); нарушение/`""` → 422 unprocessable."""
    if not (_PASSWORD_MIN_LEN <= len(password) <= _PASSWORD_MAX_LEN):
        raise unprocessable(
            "Пароль должен быть длиной 8–128 символов",
            details=[{"field": "password", "message": "Недопустимая длина пароля"}],
        )


class UserService:
    """CRUD реестра пользователей: username/email/role/пароль, bcrypt-хэш, команды."""

    def __init__(
        self,
        *,
        users: UserRepository,
        roles: RoleRepository,
        teams: TeamRepository,
    ) -> None:
        self._users = users
        self._roles = roles
        self._teams = teams

    async def list_users(self) -> UserListResponse:
        """Список пользователей (created_at ASC, id) с ролью и CRM-командами."""
        users = await self._users.list_all()
        return UserListResponse(items=[self._to_item(user) for user in users])

    async def create_user(self, payload: UserCreateRequest) -> UserListItem:
        """Создаёт пользователя. Прецеденция: username/email-формат (422) →
        существование role_id/team_ids (422) → уникальность username (409) →
        уникальность email (409)."""
        username = _validate_username(payload.username)
        email = self._normalize_optional_email(payload.email)

        role = await self._roles.get_by_id(payload.role_id)
        if role is None:
            raise unprocessable(
                "Роль не найдена",
                details=[{"field": "role_id", "message": "Роль не существует"}],
            )

        team_ids = await self._validate_team_ids(payload.team_ids)

        if await self._users.exists_by_username(username):
            raise username_taken()
        if email is not None and await self._users.exists_by_email(email):
            raise email_taken()

        try:
            user = await self._users.create(
                username=username,
                email=email,
                password_hash=hash_password(payload.password),
                role_id=payload.role_id,
            )
            await self._users.set_membership(user.id, team_ids)
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_create_conflict")
            # Гонка на уникальность username/email между проверкой и вставкой.
            if email is not None and await self._users.exists_by_email(email):
                raise email_taken() from exc
            raise username_taken() from exc

        reloaded = await self._users.get_with_teams(user.id)
        assert reloaded is not None  # только что создан в этой сессии
        logger.info("user_created", user_id=str(user.id))
        return self._to_item(reloaded)

    async def update_user(self, user_id: uuid.UUID, payload: UserUpdateRequest) -> UserListItem:
        """Редактирует email/роль/статус/пароль/команды. 404 → 422 → 409 (email).
        username не редактируется. Деактивация аннулирует JWT на следующем запросе."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise user_not_found()

        fields_set = payload.model_fields_set

        new_email: str | None = None
        clear_email = False
        if "email" in fields_set:
            if payload.email is None or payload.email == "":
                clear_email = True
            else:
                new_email = _validate_email(payload.email)

        if "role_id" in fields_set and payload.role_id is not None:
            role = await self._roles.get_by_id(payload.role_id)
            if role is None:
                raise unprocessable(
                    "Роль не найдена",
                    details=[{"field": "role_id", "message": "Роль не существует"}],
                )
            # Присваиваем связь (а не только FK), чтобы `user.role` не остался
            # устаревшим (иначе role_name в ответе показал бы старую роль).
            user.role = role

        if "password" in fields_set and payload.password is not None:
            _validate_password_reset(payload.password)

        team_ids: set[uuid.UUID] | None = None
        if "team_ids" in fields_set and payload.team_ids is not None:
            requested = await self._validate_team_ids(payload.team_ids)
            # Инвариант «лидер ∈ участники»: команды, ведомые пользователем, остаются
            # в его членстве, даже если отсутствуют в team_ids (лидер не исключается).
            team_ids = requested | await self._teams.ids_led_by(user_id)

        # Уникальность email (409) — после всех 422-валидаций.
        if new_email is not None and await self._users.exists_by_email(
            new_email, exclude_id=user_id
        ):
            raise email_taken()

        if clear_email:
            user.email = None
        elif new_email is not None:
            user.email = new_email

        if "password" in fields_set and payload.password is not None:
            user.password_hash = hash_password(payload.password)

        if "is_active" in fields_set and payload.is_active is not None:
            user.is_active = payload.is_active

        if team_ids is not None:
            await self._users.set_membership(user_id, team_ids)

        try:
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_update_conflict", user_id=str(user_id))
            raise email_taken() from exc

        reloaded = await self._users.get_with_teams(user_id)
        assert reloaded is not None  # существует (только что обновлён)
        logger.info("user_updated", user_id=str(user_id))
        return self._to_item(reloaded)

    async def delete_user(self, user_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404. Лидер команды → 409 user_is_team_leader
        (ON DELETE RESTRICT на teams.leader_id)."""
        try:
            deleted = await self._users.delete_by_id(user_id)
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_delete_is_team_leader", user_id=str(user_id))
            raise user_is_team_leader() from exc
        if not deleted:
            raise user_not_found()
        logger.info("user_deleted", user_id=str(user_id))

    @staticmethod
    def _normalize_optional_email(raw: str | None) -> str | None:
        """Опциональный email: None/`""` → None; иначе валидирует/нормализует (422)."""
        if raw is None or raw == "":
            return None
        return _validate_email(raw)

    async def _validate_team_ids(self, team_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Проверяет существование всех team_ids; несуществующие → 422. Возвращает set."""
        requested = set(team_ids)
        if not requested:
            return set()
        existing = await self._teams.get_existing_ids(requested)
        if existing != requested:
            raise unprocessable(
                "Команда не найдена",
                details=[{"field": "team_ids", "message": "Команда не существует"}],
            )
        return requested

    @staticmethod
    def _to_item(user: User) -> UserListItem:
        """Собирает элемент ответа (пароль никогда не включается; teams — CRM-команды)."""
        return UserListItem(
            id=user.id,
            username=user.username,
            email=user.email,
            role_id=user.role_id,
            role_name=user.role.name,
            is_active=user.is_active,
            teams=[TeamRef(id=team.id, name=team.name) for team in user.teams],
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
