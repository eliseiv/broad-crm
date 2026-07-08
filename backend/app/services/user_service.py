"""Бизнес-логика реестра пользователей (modules/auth, 04-api.md#users, ADR-021/022/025/026).

Пароль хранится только как bcrypt-хэш; plaintext не возвращается/не логируется. Пароль
**опционален** (беспарольный пользователь — «открытый первый вход», ADR-025). Контакт —
`telegram` (опц., заменяет прежний email). Членство в CRM-командах (`team_ids`) — ADR-022;
при исключении из команды, которую пользователь ведёт, лидерство авто-передаётся (ADR-026).
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.identity import IdentityNameError, validate_identity_name
from app.domain.telegram import TelegramFormatError, validate_telegram
from app.errors import (
    telegram_taken,
    unprocessable,
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


def _validate_telegram(raw: str) -> str:
    """Валидирует/нормализует телеграм-ник; нарушение → 422 unprocessable."""
    try:
        return validate_telegram(raw)
    except TelegramFormatError as exc:
        raise unprocessable(
            "Недопустимый телеграм-ник",
            details=[{"field": "telegram", "message": str(exc)}],
        ) from exc


def _validate_password_length(password: str) -> None:
    """Проверяет длину пароля (create с паролем / сброс через PATCH); иначе → 422."""
    if not (_PASSWORD_MIN_LEN <= len(password) <= _PASSWORD_MAX_LEN):
        raise unprocessable(
            "Пароль должен быть длиной 8–128 символов",
            details=[{"field": "password", "message": "Недопустимая длина пароля"}],
        )


class UserService:
    """CRUD реестра пользователей: username/telegram/role/пароль (опц.), команды."""

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
        """Создаёт пользователя. Прецеденция: username/telegram/password-формат (422) →
        существование role_id/team_ids (422) → уникальность username (409) →
        уникальность telegram (409). Пароль опционален (беспарольный при отсутствии)."""
        username = _validate_username(payload.username)
        telegram = self._normalize_optional_telegram(payload.telegram)
        password_hash = self._optional_password_hash(payload.password)

        role = await self._roles.get_by_id(payload.role_id)
        if role is None:
            raise unprocessable(
                "Роль не найдена",
                details=[{"field": "role_id", "message": "Роль не существует"}],
            )

        team_ids = await self._validate_team_ids(payload.team_ids)

        if await self._users.exists_by_username(username):
            raise username_taken()
        if telegram is not None and await self._users.exists_by_telegram(telegram):
            raise telegram_taken()

        try:
            user = await self._users.create(
                username=username,
                telegram=telegram,
                password_hash=password_hash,
                role_id=payload.role_id,
            )
            await self._users.set_membership(user.id, team_ids)
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_create_conflict")
            # Гонка на уникальность username/telegram между проверкой и вставкой.
            if telegram is not None and await self._users.exists_by_telegram(telegram):
                raise telegram_taken() from exc
            raise username_taken() from exc

        reloaded = await self._users.get_with_teams(user.id)
        assert reloaded is not None  # только что создан в этой сессии
        logger.info("user_created", user_id=str(user.id))
        return self._to_item(reloaded)

    async def update_user(self, user_id: uuid.UUID, payload: UserUpdateRequest) -> UserListItem:
        """Редактирует telegram/роль/статус/пароль/команды. 404 → 422 → 409 (telegram).
        username не редактируется. При исключении из ведомой команды — авто-передача
        лидерства (ADR-026). Деактивация аннулирует JWT на следующем запросе."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise user_not_found()

        fields_set = payload.model_fields_set

        new_telegram: str | None = None
        clear_telegram = False
        if "telegram" in fields_set:
            if payload.telegram is None or payload.telegram == "":
                clear_telegram = True
            else:
                new_telegram = _validate_telegram(payload.telegram)

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
            _validate_password_length(payload.password)

        requested_teams: set[uuid.UUID] | None = None
        if "team_ids" in fields_set and payload.team_ids is not None:
            requested_teams = await self._validate_team_ids(payload.team_ids)

        # Уникальность telegram (409) — после всех 422-валидаций.
        if new_telegram is not None and await self._users.exists_by_telegram(
            new_telegram, exclude_id=user_id
        ):
            raise telegram_taken()

        if clear_telegram:
            user.telegram = None
        elif new_telegram is not None:
            user.telegram = new_telegram

        if "password" in fields_set and payload.password is not None:
            user.password_hash = hash_password(payload.password)

        if "is_active" in fields_set and payload.is_active is not None:
            user.is_active = payload.is_active

        if requested_teams is not None:
            await self._replace_membership_with_transfer(user_id, requested_teams)

        try:
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_update_conflict", user_id=str(user_id))
            raise telegram_taken() from exc

        reloaded = await self._users.get_with_teams(user_id)
        assert reloaded is not None  # существует (только что обновлён)
        logger.info("user_updated", user_id=str(user_id))
        return self._to_item(reloaded)

    async def delete_user(self, user_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404. Лидерство ведомых команд авто-передаётся
        следующему участнику (или `NULL`), затем пользователь удаляется (ADR-026)."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise user_not_found()

        for team_id in await self._teams.ids_led_by(user_id):
            await self._teams.promote_next_leader(team_id, exclude_user_id=user_id)

        await self._users.delete_by_id(user_id)
        await self._users.session.commit()
        logger.info("user_deleted", user_id=str(user_id))

    async def _replace_membership_with_transfer(
        self, user_id: uuid.UUID, requested_teams: set[uuid.UUID]
    ) -> None:
        """Заменяет набор команд пользователя; при исключении из ведомой команды —
        авто-передача лидерства следующему участнику (ADR-026)."""
        current = await self._users.team_ids_of_user(user_id)
        removed = current - requested_teams
        await self._users.set_membership(user_id, requested_teams)
        if removed:
            led = await self._teams.ids_led_by(user_id)
            for team_id in led & removed:
                await self._teams.promote_next_leader(team_id, exclude_user_id=user_id)

    def _optional_password_hash(self, raw: str | None) -> str | None:
        """Опциональный пароль: None/`""` → None (беспарольный); иначе валидирует+хэширует."""
        if raw is None or raw == "":
            return None
        _validate_password_length(raw)
        return hash_password(raw)

    @staticmethod
    def _normalize_optional_telegram(raw: str | None) -> str | None:
        """Опциональный telegram: None/`""` → None; иначе валидирует/нормализует (422)."""
        if raw is None or raw == "":
            return None
        return _validate_telegram(raw)

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
            telegram=user.telegram,
            has_password=user.password_hash is not None,
            role_id=user.role_id,
            role_name=user.role.name,
            is_active=user.is_active,
            teams=[TeamRef(id=team.id, name=team.name) for team in user.teams],
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
