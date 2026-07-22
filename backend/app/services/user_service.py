"""Бизнес-логика реестра пользователей (modules/auth, 04-api.md#users, ADR-021/022/025/026).

Пароль хранится только как bcrypt-хэш; plaintext не возвращается/не логируется. Пароль
**опционален** (беспарольный пользователь — «открытый первый вход», ADR-025). Контакт —
`telegram` (опц., заменяет прежний email). Членство в CRM-командах (`team_ids`) — ADR-022;
при исключении из команды, которую пользователь ведёт, лидерство авто-передаётся (ADR-026).
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.exc import IntegrityError

from app.domain.channels import CHANNEL_MAIL, CHANNEL_SMS, CHANNELS, Channel
from app.domain.identity import IdentityNameError, validate_identity_name
from app.domain.telegram import TelegramFormatError, validate_telegram
from app.errors import (
    telegram_taken,
    unprocessable,
    user_in_use,
    user_not_found,
    username_taken,
)
from app.infra.passwords import hash_password
from app.logging import get_logger
from app.models.team import Team
from app.models.user import User
from app.repositories.mail_telegram_link_repository import MailTelegramLinkRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_channel_team_repository import UserChannelTeamRepository
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

# Имена полей доп-команд каналов в `details[].field` ошибок 422 (04-api.md#users).
_MAIL_EXTRA_FIELD = "mail_extra_team_ids"
_SMS_EXTRA_FIELD = "sms_extra_team_ids"


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
    """CRUD реестра пользователей: username/telegram/role/пароль (опц.), команды, доп-команды."""

    def __init__(
        self,
        *,
        users: UserRepository,
        roles: RoleRepository,
        teams: TeamRepository,
        channels: UserChannelTeamRepository,
    ) -> None:
        self._users = users
        self._roles = roles
        self._teams = teams
        self._channels = channels

    async def list_users(self) -> UserListResponse:
        """Список пользователей (created_at ASC, id) с ролью, командами и доп-командами.

        Доп-команды обоих каналов — ОДНИМ батч-запросом на весь список (без N+1, ADR-055 §5.2).
        """
        users = await self._users.list_all()
        extras = await self._channels.extras_for_users([user.id for user in users])
        return UserListResponse(items=[self._to_item(user, extras) for user in users])

    async def create_user(self, payload: UserCreateRequest) -> UserListItem:
        """Создаёт пользователя. Прецеденция: username/telegram/password-формат (422) →
        существование role_id/team_ids/*_extra_team_ids (422) → уникальность username (409) →
        уникальность telegram (409). Пароль опционален (беспарольный при отсутствии).

        Доп-команды каналов (ADR-055 §5.2) сохраняются **за вычетом базовых** (инвариант
        §2.3: базовые команды и так входят в scope обоих каналов) — присланная базовая
        команда в добавке не ошибка, просто не хранится."""
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
        extras = {
            CHANNEL_MAIL: await self._validate_extra_team_ids(
                payload.mail_extra_team_ids, field=_MAIL_EXTRA_FIELD
            ),
            CHANNEL_SMS: await self._validate_extra_team_ids(
                payload.sms_extra_team_ids, field=_SMS_EXTRA_FIELD
            ),
        }

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
            user.mail_includes_unassigned = payload.mail_extra_includes_unassigned
            user.sms_includes_unassigned = payload.sms_extra_includes_unassigned
            await self._users.set_membership(user.id, team_ids)
            for channel, extra_ids in extras.items():
                # Инвариант §2.3: в добавке не хранятся базовые команды.
                await self._channels.replace_extras(user.id, channel, extra_ids - team_ids)
            if telegram is not None:
                # Ленивый резолв orphan-линков почты (ADR-044 §6, синхронный хук):
                # связать привязки с этим username, ожидавшие появления пользователя.
                await MailTelegramLinkRepository(self._users.session).bind_orphans_for_user(
                    user_id=user.id, username=telegram
                )
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
        return await self._to_item_reloaded(reloaded)

    async def update_user(self, user_id: uuid.UUID, payload: UserUpdateRequest) -> UserListItem:
        """Редактирует telegram/роль/статус/пароль/команды/доп-команды каналов.
        404 → 422 → 409 (telegram). username не редактируется. При исключении из ведомой
        команды — авто-передача лидерства (ADR-026). Деактивация аннулирует JWT на
        следующем запросе.

        Доп-команды каналов (ADR-055 §5.2/§2.3): поле не передано → набор канала не менять;
        передано → полностью заменить. Из сохраняемого набора **вычитается** эффективный
        базовый набор (`team_ids` этого запроса, иначе — текущее членство) ⇒ команда,
        добавленная в основной блок, не остаётся дублем в добавке, а исключение из команды
        не оставляет «висящего» доступа к каналу."""
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

        requested_extras: dict[Channel, set[uuid.UUID]] = {}
        if "mail_extra_team_ids" in fields_set and payload.mail_extra_team_ids is not None:
            requested_extras[CHANNEL_MAIL] = await self._validate_extra_team_ids(
                payload.mail_extra_team_ids, field=_MAIL_EXTRA_FIELD
            )
        if "sms_extra_team_ids" in fields_set and payload.sms_extra_team_ids is not None:
            requested_extras[CHANNEL_SMS] = await self._validate_extra_team_ids(
                payload.sms_extra_team_ids, field=_SMS_EXTRA_FIELD
            )

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

        if (
            "mail_extra_includes_unassigned" in fields_set
            and payload.mail_extra_includes_unassigned is not None
        ):
            user.mail_includes_unassigned = payload.mail_extra_includes_unassigned
        if (
            "sms_extra_includes_unassigned" in fields_set
            and payload.sms_extra_includes_unassigned is not None
        ):
            user.sms_includes_unassigned = payload.sms_extra_includes_unassigned

        if requested_teams is not None:
            await self._replace_membership_with_transfer(user_id, requested_teams)

        await self._normalize_extras(
            user_id,
            requested_teams=requested_teams,
            requested_extras=requested_extras,
        )

        if new_telegram is not None:
            # Ленивый резолв orphan-линков почты (ADR-044 §6): смена users.telegram
            # связывает ожидавшие привязки без повторного /start.
            await MailTelegramLinkRepository(self._users.session).bind_orphans_for_user(
                user_id=user_id, username=new_telegram
            )

        try:
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_update_conflict", user_id=str(user_id))
            raise telegram_taken() from exc

        reloaded = await self._users.get_with_teams(user_id)
        assert reloaded is not None  # существует (только что обновлён)
        logger.info("user_updated", user_id=str(user_id))
        return await self._to_item_reloaded(reloaded)

    async def delete_user(self, user_id: uuid.UUID) -> None:
        """Hard-delete; повтор → 404. Лидерство ведомых команд авто-передаётся
        следующему участнику (или `NULL`), затем пользователь удаляется (ADR-026).

        **Пользователя может держать FK `ON DELETE RESTRICT`** (`document_nodes.owner_id`,
        `document_attachments.created_by` — ADR-059/ADR-068): тогда `commit` даёт
        `IntegrityError`, и исход обязан быть прикладным **`409 user_in_use`**, а НЕ
        `500 internal_error` (04-api.md#delete-apiusersid, TD-077) — тот же принцип, что
        `409 role_in_use` для `users.role_id`.

        Перехват исключения, а не проактивный `EXISTS`: перечень FK `RESTRICT` на
        `users.id` растёт (ADR-059 → ADR-068), и предварительная проверка рассинхронизируется
        с ним молча, а перехват — нет. `rollback` откатывает и авто-передачу лидерства (она
        идёт в этой же транзакции ДО удаления, ADR-026) ⇒ после `409` состояние БД не
        изменено вовсе, частичного эффекта нет. Состав удерживающих узлов в ответе НЕ
        раскрывается — анти-энумерация модуля «Документы» (ADR-059) не ослабляется.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise user_not_found()

        for team_id in await self._teams.ids_led_by(user_id):
            await self._teams.promote_next_leader(team_id, exclude_user_id=user_id)

        try:
            # Перехват охватывает и сам `DELETE`, и `commit`: FK не `DEFERRABLE`, поэтому
            # `RESTRICT` срабатывает уже на выполнении statement'а, а не на фиксации
            # (проверено на Postgres 16 — иначе исключение прошло бы мимо и дало 500).
            await self._users.delete_by_id(user_id)
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_delete_restricted", user_id=str(user_id))
            raise user_in_use() from exc
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

    async def _normalize_extras(
        self,
        user_id: uuid.UUID,
        *,
        requested_teams: set[uuid.UUID] | None,
        requested_extras: dict[Channel, set[uuid.UUID]],
    ) -> None:
        """Приводит добавки каналов к инварианту §2.3 (путь 1 — users CRUD).

        Эффективный базовый набор = присланный `team_ids` (если поле было в теле), иначе
        текущее членство. Для канала берётся присланная добавка (если поле было), иначе —
        уже хранимая; из неё **вычитается** базовый набор, и результат сохраняется.
        Ничего не передано (ни `team_ids`, ни добавки) → запись не выполняется.
        """
        if requested_teams is None and not requested_extras:
            return
        base = (
            requested_teams
            if requested_teams is not None
            else await self._users.team_ids_of_user(user_id)
        )
        for channel in CHANNELS:
            if channel in requested_extras:
                target = requested_extras[channel]
            elif requested_teams is not None:
                # Базовый набор изменился, добавка — нет: снять из неё ставшие базовыми
                # команды (иначе инвариант §2.3 нарушился бы дублем).
                target = await self._channels.extra_team_ids(user_id, channel)
            else:
                continue
            await self._channels.replace_extras(user_id, channel, target - base)

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

    async def _validate_extra_team_ids(
        self, team_ids: list[uuid.UUID], *, field: str
    ) -> set[uuid.UUID]:
        """Существование всех доп-команд канала; несуществующие → 422 с именем поля (§5.2).

        Пересечение с базовыми `team_ids` **не** проверяется и ошибкой НЕ является — его
        вычитает `_normalize_extras` (инвариант §2.3).
        """
        requested = set(team_ids)
        if not requested:
            return set()
        existing = await self._teams.get_existing_ids(requested)
        if existing != requested:
            raise unprocessable(
                "Команда не найдена",
                details=[{"field": field, "message": "Команда не существует"}],
            )
        return requested

    @staticmethod
    def _derive_status(user: User) -> Literal["pending", "active", "inactive"]:
        """Производный тристатус (ADR-028, нормативно, приоритет `is_active`):

        `is_active=false` → `"inactive"`; `is_active=true` И `first_login_at IS NULL` →
        `"pending"`; иначе (активен И входил хотя бы раз) → `"active"`.
        """
        if not user.is_active:
            return "inactive"
        if user.first_login_at is None:
            return "pending"
        return "active"

    async def _to_item_reloaded(self, user: User) -> UserListItem:
        """Элемент ответа 201/200 (одиночный пользователь): добавки читаются точечно."""
        extras = await self._channels.extras_for_users([user.id])
        return self._to_item(user, extras)

    @staticmethod
    def _to_item(user: User, extras: dict[tuple[uuid.UUID, str], list[Team]]) -> UserListItem:
        """Собирает элемент ответа (пароль никогда не включается; teams — CRM-команды).

        `*_extra_teams` — ТОЛЬКО хранимая добавка канала (без базовых команд, ADR-055 §5.2);
        `*_extra_includes_unassigned` — колонки `users.<channel>_includes_unassigned`.
        """
        return UserListItem(
            id=user.id,
            username=user.username,
            telegram=user.telegram,
            has_password=user.password_hash is not None,
            role_id=user.role_id,
            role_name=user.role.name,
            is_active=user.is_active,
            status=UserService._derive_status(user),
            teams=[TeamRef(id=team.id, name=team.name) for team in user.teams],
            mail_extra_teams=UserService._team_refs(extras.get((user.id, CHANNEL_MAIL), [])),
            mail_extra_includes_unassigned=user.mail_includes_unassigned,
            sms_extra_teams=UserService._team_refs(extras.get((user.id, CHANNEL_SMS), [])),
            sms_extra_includes_unassigned=user.sms_includes_unassigned,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    @staticmethod
    def _team_refs(teams: list[Team]) -> list[TeamRef]:
        """`TeamRef[]` доп-команд, отсортированный по `name` (ru, ci — `casefold`)."""
        refs = [TeamRef(id=team.id, name=team.name) for team in teams]
        refs.sort(key=lambda ref: ref.name.casefold())
        return refs
