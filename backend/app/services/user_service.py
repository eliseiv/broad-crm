"""Бизнес-логика реестра пользователей (modules/auth, 04-api.md#users, ADR-021/022/025/026/079).

Пароль хранится только как bcrypt-хэш; plaintext не возвращается/не логируется. Пароль
**опционален** (беспарольный пользователь — «открытый первый вход», ADR-025).

**ADR-079:** роли — M2M (`role_ids`, минимум одна — инвариант СЕРВИСА, 422); ФИО
(`last_name`/`first_name` обязательны, `middle_name` опционально); `telegram`
**обязателен** при создании и **не очищается** через `PATCH`; `username` из формы удалён —
сервис выводит его из телеграм-ника при создании и **не пересчитывает** при смене
телеграма (стабильность `sub` уже выпущенных токенов).

Членство в CRM-командах (`team_ids`) — ADR-022; при исключении из команды, которую
пользователь ведёт, лидерство авто-передаётся (ADR-026).
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy.exc import IntegrityError

from app.domain.channels import CHANNEL_MAIL, CHANNEL_SMS, CHANNELS, Channel
from app.domain.identity import IdentityNameError, validate_identity_name
from app.domain.permissions import (
    full_catalog_permissions,
    permissions_subset,
    union_permissions,
)
from app.domain.telegram import TelegramFormatError, validate_telegram
from app.errors import (
    forbidden,
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
from app.repositories.knowledge_bot_link_repository import KnowledgeBotLinkRepository
from app.repositories.mail_telegram_link_repository import MailTelegramLinkRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_channel_team_repository import UserChannelTeamRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import (
    RoleRef,
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


def _validate_name_part(raw: str, *, field: str) -> str:
    """Валидирует/нормализует часть ФИО; нарушение → 422 unprocessable (ADR-079 §7).

    Правило то же, что у `username` (кириллица-допускающее, 03-data-model.md) — оно
    переиспользуется намеренно, чтобы не заводить второй набор допустимых символов.
    """
    try:
        return validate_identity_name(raw)
    except IdentityNameError as exc:
        raise unprocessable(
            "Недопустимая часть ФИО",
            details=[{"field": field, "message": str(exc)}],
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
    """CRUD реестра пользователей: ФИО/telegram/роли (M2M)/пароль (опц.), команды, доп-команды."""

    def __init__(
        self,
        *,
        users: UserRepository,
        roles: RoleRepository,
        teams: TeamRepository,
        channels: UserChannelTeamRepository,
        knowledge_bot_links: KnowledgeBotLinkRepository,
    ) -> None:
        self._users = users
        self._roles = roles
        self._teams = teams
        self._channels = channels
        self._knowledge_bot_links = knowledge_bot_links

    async def list_users(self) -> UserListResponse:
        """Список пользователей (created_at ASC, id) с ролью, командами и доп-командами.

        Доп-команды обоих каналов — ОДНИМ батч-запросом на весь список (без N+1, ADR-055 §5.2).
        """
        users = await self._users.list_all()
        extras = await self._channels.extras_for_users([user.id for user in users])
        started_ids = await self._knowledge_bot_links.active_user_ids([user.id for user in users])
        return UserListResponse(
            items=[
                self._to_item(user, extras, bot_started=user.id in started_ids) for user in users
            ]
        )

    async def create_user(
        self,
        payload: UserCreateRequest,
        *,
        actor_permissions: dict[str, list[str]] | None = None,
        actor_privileged: bool = True,
    ) -> UserListItem:
        """Создаёт пользователя. Прецеденция (ADR-079 §9, нормативно): формат ФИО/
        telegram/password (422) → непустота и существование `role_ids`, существование
        `team_ids`/`*_extra_team_ids` (422) → **уникальность telegram (409
        telegram_taken)** → уникальность выведенного username (409 username_taken).

        **`username` не приходит из формы** — сервис выводит его из телеграм-ника
        (`normalize_telegram`, §9): поля «Логин» в UI больше нет. Порядок двух `409`
        изменён ADR-079 §9: оба конфликта порождены ОДНИМ введённым значением, поэтому
        первой называется прямая причина (`telegram_taken`), а не побочная.

        Пароль опционален (беспарольный при отсутствии). Доп-команды каналов (ADR-055
        §5.2) сохраняются **за вычетом базовых** (инвариант §2.3: базовые команды и так
        входят в scope обоих каналов) — присланная базовая команда в добавке не ошибка,
        просто не хранится."""
        last_name = _validate_name_part(payload.last_name, field="last_name")
        first_name = _validate_name_part(payload.first_name, field="first_name")
        middle_name = self._normalize_optional_middle_name(payload.middle_name)
        telegram = self._require_telegram(payload.telegram)
        # `username` выводится из телеграм-ника и больше не вводится оператором (§9).
        username = telegram
        password_hash = self._optional_password_hash(payload.password)

        role_ids = await self._validate_role_ids(payload.role_ids)
        await self._assert_roles_within_actor(
            role_ids,
            actor_permissions=actor_permissions or {},
            actor_privileged=actor_privileged,
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

        # Прецеденция ADR-079 §9: telegram_taken ПРЕЖДЕ username_taken.
        if await self._users.exists_by_telegram(telegram):
            raise telegram_taken()
        if await self._users.exists_by_username(username):
            raise username_taken()

        try:
            user = await self._users.create(
                username=username,
                telegram=telegram,
                password_hash=password_hash,
                last_name=last_name,
                first_name=first_name,
                middle_name=middle_name,
            )
            await self._users.set_roles(user.id, role_ids)
            user.mail_includes_unassigned = payload.mail_extra_includes_unassigned
            user.sms_includes_unassigned = payload.sms_extra_includes_unassigned
            await self._users.set_membership(user.id, team_ids)
            for channel, extra_ids in extras.items():
                # Инвариант §2.3: в добавке не хранятся базовые команды.
                await self._channels.replace_extras(user.id, channel, extra_ids - team_ids)
            # Ленивый резолв orphan-линков почты (ADR-044 §6, синхронный хук):
            # связать привязки с этим username, ожидавшие появления пользователя.
            await MailTelegramLinkRepository(self._users.session).bind_orphans_for_user(
                user_id=user.id, username=telegram
            )
            await self._users.session.commit()
        except IntegrityError as exc:
            await self._users.session.rollback()
            logger.info("user_create_conflict")
            # Гонка на уникальность telegram/username между проверкой и вставкой —
            # тот же порядок исходов, что и у проактивных проверок выше.
            if await self._users.exists_by_telegram(telegram):
                raise telegram_taken() from exc
            raise username_taken() from exc

        reloaded = await self._users.get_with_teams(user.id)
        assert reloaded is not None  # только что создан в этой сессии
        logger.info("user_created", user_id=str(user.id))
        return await self._to_item_reloaded(reloaded)

    async def update_user(
        self,
        user_id: uuid.UUID,
        payload: UserUpdateRequest,
        *,
        actor_permissions: dict[str, list[str]] | None = None,
        actor_privileged: bool = True,
    ) -> UserListItem:
        """Редактирует ФИО/telegram/роли/статус/пароль/команды/доп-команды каналов.
        404 → 422 → 409 (telegram).

        **`username` не редактируется и НЕ пересчитывается при смене telegram**
        (ADR-079 §9): иначе поменялся бы `sub` уже выпущенных токенов и ключ
        bootstrap-резолва внешнего контура. Поэтому `username_taken` здесь недостижим.

        ФИО (§7): `last_name`/`first_name` — очистка (`null`/`""`) запрещена (422);
        `middle_name` — единственная снимаемая часть. `telegram` (§8) — **очистка
        запрещена** (422): поля «Логин» в UI нет, пользователь остался бы без способа
        входа. `role_ids` — полная замена набора, `[]` → 422.

        При исключении из ведомой команды — авто-передача лидерства (ADR-026).
        Деактивация аннулирует JWT на следующем запросе.

        Доп-команды каналов (ADR-055 §5.2/§2.3): поле не передано → набор канала не менять;
        передано → полностью заменить. Из сохраняемого набора **вычитается** эффективный
        базовый набор (`team_ids` этого запроса, иначе — текущее членство) ⇒ команда,
        добавленная в основной блок, не остаётся дублем в добавке, а исключение из команды
        не оставляет «висящего» доступа к каналу."""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise user_not_found()
        self._assert_can_touch(user, actor_privileged=actor_privileged)

        fields_set = payload.model_fields_set

        new_last_name = (
            self._require_name_part(payload.last_name, field="last_name")
            if "last_name" in fields_set
            else None
        )
        new_first_name = (
            self._require_name_part(payload.first_name, field="first_name")
            if "first_name" in fields_set
            else None
        )
        # Отчество — единственная часть ФИО, которую можно снять (`null`/`""` → NULL).
        middle_name_touched = "middle_name" in fields_set
        new_middle_name = (
            self._normalize_optional_middle_name(payload.middle_name)
            if middle_name_touched
            else None
        )

        new_telegram: str | None = None
        if "telegram" in fields_set:
            # Очистка запрещена (ADR-079 §8): `null`/`""` → 422, а не «убрать телеграм».
            new_telegram = self._require_telegram(payload.telegram)

        requested_roles: set[uuid.UUID] | None = None
        if "role_ids" in fields_set:
            if payload.role_ids is None:
                raise unprocessable(
                    "Нужна хотя бы одна роль",
                    details=[{"field": "role_ids", "message": "Список ролей пуст"}],
                )
            requested_roles = await self._validate_role_ids(payload.role_ids)
            await self._assert_roles_within_actor(
                requested_roles,
                actor_permissions=actor_permissions or {},
                actor_privileged=actor_privileged,
            )

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

        if new_last_name is not None:
            user.last_name = new_last_name
        if new_first_name is not None:
            user.first_name = new_first_name
        if middle_name_touched:
            user.middle_name = new_middle_name

        if new_telegram is not None:
            user.telegram = new_telegram

        if requested_roles is not None:
            await self._users.set_roles(user_id, requested_roles)

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

    async def delete_user(self, user_id: uuid.UUID, *, actor_privileged: bool = True) -> None:
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
        self._assert_can_touch(user, actor_privileged=actor_privileged)

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
    def _require_telegram(raw: str | None) -> str:
        """Обязательный telegram (ADR-079 §8): None/`""` → 422; иначе нормализованный канон.

        Общий валидатор для `POST` (поле обязательно) и `PATCH` (очистка **запрещена** —
        прежняя норма «`null`/`""` → убрать телеграм» отменена).
        """
        if raw is None or raw.strip() == "":
            raise unprocessable(
                "Телеграм-ник обязателен",
                details=[{"field": "telegram", "message": "Телеграм-ник не задан"}],
            )
        return _validate_telegram(raw)

    @staticmethod
    def _require_name_part(raw: str | None, *, field: str) -> str:
        """Обязательная часть ФИО в `PATCH` (ADR-079 §7): `null`/`""` → 422."""
        if raw is None or raw.strip() == "":
            raise unprocessable(
                "Часть ФИО обязательна",
                details=[{"field": field, "message": "Значение не задано"}],
            )
        return _validate_name_part(raw, field=field)

    @staticmethod
    def _normalize_optional_middle_name(raw: str | None) -> str | None:
        """Отчество: None/`""` → None (снять); иначе валидирует/нормализует (422)."""
        if raw is None or raw.strip() == "":
            return None
        return _validate_name_part(raw, field="middle_name")

    async def reset_password(
        self, user_id: uuid.UUID, *, actor_privileged: bool = True
    ) -> UserListItem:
        """Сброс пароля к «открытому первому входу» (ADR-025): `password_hash → NULL`.

        `first_login_at` тоже гасится — иначе тристатус (ADR-028) показывал бы
        «Активен» пользователю, который пароль ещё не задал. После сброса вход по
        логину/телеграму без пароля выдаёт setup-token и форму «задайте пароль» —
        ровно тот же сценарий, что у нового сотрудника.

        Системный якорь недостижим (`get_by_id` фильтрует `NOT is_system`) → 404.

        **Сброс — вектор эскалации, а не безобидная операция:** беспарольный вход
        открыт любому, кто знает логин/телеграм жертвы (ADR-025), поэтому сбросить
        пароль admin-level пользователю непривилегированный актор не может — иначе
        право `users:edit` превращалось бы в захват админской учётки.
        """
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise user_not_found()
        self._assert_can_touch(user, actor_privileged=actor_privileged)

        user.password_hash = None
        user.first_login_at = None
        await self._users.session.commit()
        logger.info("user_password_reset", user_id=str(user.id))
        return await self._to_item_reloaded(user)

    async def _assert_roles_within_actor(
        self,
        role_ids: set[uuid.UUID],
        *,
        actor_permissions: dict[str, list[str]],
        actor_privileged: bool,
    ) -> None:
        """Security-инвариант эскалации для реестра пользователей (зеркало ADR-022 §роли).

        Страница `users` вошла в матрицу прав, поэтому право `users:create|edit` может
        быть у НЕ-админа. Без этой проверки такой актор выдал бы себе (или новому
        пользователю) роль «Админ» и поднял бы привилегии — то есть матрица заменила
        бы собой всю модель доступа. Привилегированный актор (`is_admin_level`)
        не ограничен.
        """
        if actor_privileged:
            return
        for role_id in sorted(role_ids):
            role = await self._roles.get_by_id(role_id)
            if role is None:
                continue
            if not permissions_subset(dict(role.permissions or {}), actor_permissions):
                raise forbidden()

    @staticmethod
    def _is_admin_level_user(user: User) -> bool:
        """Целевой пользователь admin-уровня: роль «Админ» ИЛИ union == полный каталог."""
        roles = list(user.roles or [])
        if any(role.name.strip().lower() == "admin" for role in roles):
            return True
        union = union_permissions(dict(role.permissions or {}) for role in roles)
        return permissions_subset(full_catalog_permissions(), union)

    def _assert_can_touch(self, user: User, *, actor_privileged: bool) -> None:
        """Непривилегированный актор не редактирует/не удаляет admin-level пользователя."""
        if actor_privileged:
            return
        if self._is_admin_level_user(user):
            raise forbidden()

    async def _validate_role_ids(self, role_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Непустой набор существующих ролей (ADR-079 §1); иначе → 422.

        «Минимум одна роль» — инвариант **сервиса**, а не БД: выразить «≥1 строка в
        дочерней таблице» без триггера/`DEFERRABLE`-констрейнта нельзя, а триггеров в
        проекте нет ни одного.
        """
        requested = set(role_ids)
        if not requested:
            raise unprocessable(
                "Нужна хотя бы одна роль",
                details=[{"field": "role_ids", "message": "Список ролей пуст"}],
            )
        existing = await self._roles.existing_ids(requested)
        if existing != requested:
            raise unprocessable(
                "Роль не найдена",
                details=[{"field": "role_ids", "message": "Роль не существует"}],
            )
        return requested

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
        bot_started = await self._knowledge_bot_links.exists_for_user(user.id)
        return self._to_item(user, extras, bot_started=bot_started)

    @staticmethod
    def _to_item(
        user: User,
        extras: dict[tuple[uuid.UUID, str], list[Team]],
        *,
        bot_started: bool,
    ) -> UserListItem:
        """Собирает элемент ответа (пароль никогда не включается; teams — CRM-команды).

        `*_extra_teams` — ТОЛЬКО хранимая добавка канала (без базовых команд, ADR-055 §5.2);
        `*_extra_includes_unassigned` — колонки `users.<channel>_includes_unassigned`.
        `bot_started` — EXISTS активный `knowledge_bot_links` (ADR-076).
        """
        return UserListItem(
            id=user.id,
            username=user.username,
            last_name=user.last_name,
            first_name=user.first_name,
            middle_name=user.middle_name,
            telegram=user.telegram,
            has_password=user.password_hash is not None,
            roles=[RoleRef(id=role.id, name=role.name) for role in user.roles],
            is_active=user.is_active,
            status=UserService._derive_status(user),
            teams=[TeamRef(id=team.id, name=team.name) for team in user.teams],
            mail_extra_teams=UserService._team_refs(extras.get((user.id, CHANNEL_MAIL), [])),
            mail_extra_includes_unassigned=user.mail_includes_unassigned,
            sms_extra_teams=UserService._team_refs(extras.get((user.id, CHANNEL_SMS), [])),
            sms_extra_includes_unassigned=user.sms_includes_unassigned,
            bot_started=bot_started,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )

    @staticmethod
    def _team_refs(teams: list[Team]) -> list[TeamRef]:
        """`TeamRef[]` доп-команд, отсортированный по `name` (ru, ci — `casefold`)."""
        refs = [TeamRef(id=team.id, name=team.name) for team in teams]
        refs.sort(key=lambda ref: ref.name.casefold())
        return refs
