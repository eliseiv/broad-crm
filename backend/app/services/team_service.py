"""Бизнес-логика реестра CRM-команд (modules/teams, 04-api.md#teams, ADR-022/026).

Лидер **опционален** (`leader_id` nullable): команда может быть без лидера и без
участников. Инвариант «если лидер задан — он ∈ участники» обеспечивается на всех путях
записи. Авто-назначение: при отсутствии лидера первый участник (по дате добавления)
становится лидером. Авто-передача: при исключении текущего лидера лидерство переходит
следующему по `user_teams.created_at` (или `leader_id → NULL`, если участников не
осталось). Существование `leader_id`/`member_ids` валидирует `UserRepository` → 422.
Уникальность `name` → 409 team_name_taken.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.identity import IdentityNameError, validate_identity_name
from app.errors import (
    team_mail_group_taken,
    team_name_taken,
    team_not_found,
    unprocessable,
)
from app.logging import get_logger
from app.models.team import Team
from app.repositories.mail_account_repository import MailAccountRepository
from app.repositories.sms_number_repository import SmsNumberRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_repository import UserRepository
from app.schemas.sms import TeamNumbersResponse
from app.schemas.team import (
    TeamCreateRequest,
    TeamListItem,
    TeamListResponse,
    TeamMember,
    TeamUpdateRequest,
)
from app.services.sms_serialize import to_team_number_item

logger = get_logger(__name__)

# Имя UNIQUE-констрейнта teams.mail_group_id (миграция 0018,
# models/team.py::UniqueConstraint) — для различения гонки name↔mail_group.
_MAIL_GROUP_CONSTRAINT = "uq_teams_mail_group_id"


def _violated_constraint(exc: IntegrityError) -> str | None:
    """Имя нарушенного констрейнта из исключения драйвера (structured, без парсинга текста).

    asyncpg наполняет `UniqueViolationError.constraint_name` из полей серверной
    ошибки; SQLAlchemy оборачивает её в `IntegrityError.orig`. Идём по цепочке
    `orig`→`__cause__` и берём первый доступный `constraint_name`. Если драйвер его не
    отдал (None) — вызывающий переходит на структурный фолбэк (`exists_by_*`).
    """
    candidate: BaseException | None = exc.orig
    for _ in range(4):
        if candidate is None:
            break
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str) and name:
            return name
        candidate = getattr(candidate, "__cause__", None)
    return None


def _validate_name(raw: str) -> str:
    """Валидирует/нормализует имя команды (формат как username); нарушение → 422."""
    try:
        return validate_identity_name(raw)
    except IdentityNameError as exc:
        raise unprocessable(
            "Недопустимое название команды",
            details=[{"field": "name", "message": str(exc)}],
        ) from exc


class TeamService:
    """CRUD реестра CRM-команд: опциональный лидер, авто-назначение/передача, валидация."""

    def __init__(
        self,
        *,
        teams: TeamRepository,
        users: UserRepository,
        numbers: SmsNumberRepository,
        mailboxes: MailAccountRepository,
    ) -> None:
        self._teams = teams
        self._users = users
        self._numbers = numbers
        self._mailboxes = mailboxes

    async def list_teams(self) -> TeamListResponse:
        """Список команд (created_at DESC, id): лидер, участники, number_count, mailbox_count.

        Оба счётчика — батч-агрегаты (`count_by_teams`), без N+1 (ADR-030, ADR-048 §1).
        """
        teams = await self._teams.list_all()
        team_ids = [team.id for team in teams]
        number_counts = await self._numbers.count_by_teams(team_ids)
        mailbox_counts = await self._mailboxes.count_by_teams(team_ids)
        return TeamListResponse(
            items=[
                self._to_item(
                    team,
                    number_counts.get(team.id, 0),
                    mailbox_counts.get(team.id, 0),
                )
                for team in teams
            ]
        )

    async def list_team_numbers(self, team_id: uuid.UUID) -> TeamNumbersResponse:
        """Список SMS-номеров команды (detail-панель /teams, ADR-030). Нет команды → 404."""
        if not await self._teams.get_existing_ids({team_id}):
            raise team_not_found()
        numbers = await self._numbers.list_by_team(team_id)
        return TeamNumbersResponse(numbers=[to_team_number_item(n) for n in numbers])

    async def get_team_mail_group_id(self, team_id: uuid.UUID) -> int | None:
        """Резолв `teams.mail_group_id` для секции «Почты команды» (ADR-038). Нет → 404."""
        team = await self._teams.get(team_id)
        if team is None:
            raise team_not_found()
        return team.mail_group_id

    async def ensure_team_exists(self, team_id: uuid.UUID) -> None:
        """Команда существует в CRM, иначе 404 team_not_found (ADR-044 §4)."""
        if not await self._teams.get_existing_ids({team_id}):
            raise team_not_found()

    async def create_team(self, payload: TeamCreateRequest) -> TeamListItem:
        """Создаёт команду. Прецеденция: name-формат (422) → существование
        leader_id/member_ids (422) → уникальность name (409). Лидер (если есть) — в
        участники; авто-назначение первого участника лидером при отсутствии лидера."""
        name = _validate_name(payload.name)

        if payload.leader_id is not None:
            await self._require_user_exists(payload.leader_id, field="leader_id")
        await self._validate_members(payload.member_ids)

        if await self._teams.exists_by_name(name):
            raise team_name_taken()

        if payload.mail_group_id is not None and await self._teams.exists_by_mail_group_id(
            payload.mail_group_id
        ):
            raise team_mail_group_taken()

        ordered = list(dict.fromkeys(payload.member_ids))
        # Инвариант «лидер ∈ участники»: заданный лидер добавляется в состав.
        if payload.leader_id is not None and payload.leader_id not in ordered:
            ordered.append(payload.leader_id)
        # Авто-назначение: лидер не задан, но есть участники → первый становится лидером.
        leader_id = payload.leader_id
        if leader_id is None and ordered:
            leader_id = ordered[0]

        try:
            team = await self._teams.create(
                name=name,
                leader_id=leader_id,
                ordered_member_ids=ordered,
                mail_group_id=payload.mail_group_id,
            )
            await self._teams.session.commit()
        except IntegrityError as exc:
            await self._teams.session.rollback()
            if await self._is_mail_group_conflict(
                exc, mail_group_id=payload.mail_group_id, exclude_id=None
            ):
                logger.info("team_create_conflict_mail_group")
                raise team_mail_group_taken() from exc
            logger.info("team_create_conflict", name=name)
            raise team_name_taken() from exc

        reloaded = await self._teams.get_with_members(team.id)
        assert reloaded is not None  # только что создана в этой сессии
        number_count = await self._numbers.count_by_team(reloaded.id)
        mailbox_count = await self._mailboxes.count_by_team(reloaded.id)
        logger.info("team_created", team_id=str(team.id))
        return self._to_item(reloaded, number_count, mailbox_count)

    async def update_team(self, team_id: uuid.UUID, payload: TeamUpdateRequest) -> TeamListItem:
        """Редактирует команду. Прецеденция: 404 → name-формат (422) → существование
        leader_id/member_ids (422) → уникальность name (409). Инвариант «лидер ∈
        участники»; авто-передача/авто-назначение лидерства (ADR-026)."""
        team = await self._teams.get_with_members(team_id)
        if team is None:
            raise team_not_found()

        fields_set = payload.model_fields_set

        new_name: str | None = None
        if "name" in fields_set and payload.name is not None:
            new_name = _validate_name(payload.name)

        leader_provided = "leader_id" in fields_set
        if leader_provided and payload.leader_id is not None:
            await self._require_user_exists(payload.leader_id, field="leader_id")

        members_provided = "member_ids" in fields_set and payload.member_ids is not None
        if members_provided:
            assert payload.member_ids is not None
            await self._validate_members(payload.member_ids)

        # Уникальность имени (409) — после 422-валидаций.
        if new_name is not None and new_name != team.name:
            if await self._teams.exists_by_name(new_name, exclude_id=team.id):
                raise team_name_taken()
            team.name = new_name

        # Привязка к группе mail-агрегатора (presence-семантика, ADR-038): передано →
        # изменить (int — привязать/сменить, null — снять); занято → 409. Захватываем
        # plain-int локально для фолбэка гонки в IntegrityError (после rollback ORM-атрибут
        # team.mail_group_id expired — читать нельзя в async).
        submitted_mail_group_id: int | None = None
        if "mail_group_id" in fields_set:
            new_group_id = payload.mail_group_id
            submitted_mail_group_id = new_group_id
            if (
                new_group_id is not None
                and new_group_id != team.mail_group_id
                and await self._teams.exists_by_mail_group_id(new_group_id, exclude_id=team.id)
            ):
                raise team_mail_group_taken()
            team.mail_group_id = new_group_id

        old_leader_id = team.leader_id

        # Целевой состав участников (с гарантией «лидер ∈ участники», если лидер — uuid).
        ordered_desired: list[uuid.UUID] | None = None
        if members_provided:
            assert payload.member_ids is not None
            ordered_desired = list(dict.fromkeys(payload.member_ids))
        if leader_provided and payload.leader_id is not None:
            if ordered_desired is None:
                ordered_desired = [member.id for member in team.members]
            if payload.leader_id not in ordered_desired:
                ordered_desired.append(payload.leader_id)

        if ordered_desired is not None:
            await self._teams.replace_members(team.id, ordered_desired)
            await self._teams.session.flush()

        # Определение лидера после приведения состава.
        new_leader_id = await self._resolve_leader(
            team_id=team.id,
            leader_provided=leader_provided,
            payload_leader_id=payload.leader_id,
            old_leader_id=old_leader_id,
            ordered_desired=ordered_desired,
        )
        team.leader_id = new_leader_id

        try:
            await self._teams.session.commit()
        except IntegrityError as exc:
            await self._teams.session.rollback()
            if await self._is_mail_group_conflict(
                exc, mail_group_id=submitted_mail_group_id, exclude_id=team_id
            ):
                logger.info("team_update_conflict_mail_group", team_id=str(team_id))
                raise team_mail_group_taken() from exc
            logger.info("team_update_conflict", team_id=str(team_id))
            raise team_name_taken() from exc

        reloaded = await self._teams.get_with_members(team_id)
        assert reloaded is not None  # существует (только что обновлена)
        number_count = await self._numbers.count_by_team(reloaded.id)
        mailbox_count = await self._mailboxes.count_by_team(reloaded.id)
        logger.info("team_updated", team_id=str(team_id))
        return self._to_item(reloaded, number_count, mailbox_count)

    async def delete_team(self, team_id: uuid.UUID) -> None:
        """Hard-delete (каскад `user_teams`); повтор → 404 team_not_found."""
        deleted = await self._teams.delete_by_id(team_id)
        if not deleted:
            raise team_not_found()
        await self._teams.session.commit()
        logger.info("team_deleted", team_id=str(team_id))

    async def _resolve_leader(
        self,
        *,
        team_id: uuid.UUID,
        leader_provided: bool,
        payload_leader_id: uuid.UUID | None,
        old_leader_id: uuid.UUID | None,
        ordered_desired: list[uuid.UUID] | None,
    ) -> uuid.UUID | None:
        """Вычисляет нового лидера после правки состава (ADR-026, авто-передача/назначение).

        - `leader_id` передан (uuid|null) → он лидер (uuid ∈ участники гарантирован ранее);
        - `leader_id` не передан, состав НЕ менялся → лидер без изменений;
        - `leader_id` не передан, состав менялся → текущий лидер, если ещё участник; иначе
          первый по `user_teams.created_at` (или None, если участников нет).
        """
        if leader_provided:
            return payload_leader_id
        if ordered_desired is None:
            return old_leader_id
        if old_leader_id is not None and old_leader_id in set(ordered_desired):
            return old_leader_id
        return await self._teams.get_first_member(team_id)

    async def _is_mail_group_conflict(
        self,
        exc: IntegrityError,
        *,
        mail_group_id: int | None,
        exclude_id: uuid.UUID | None,
    ) -> bool:
        """Сработал ли на гонке констрейнт `uq_teams_mail_group_id` (а не name-UNIQUE).

        Приоритет — структурное имя констрейнта из драйвера (`_violated_constraint`).
        Если драйвер имя не отдал (None) — фолбэк: перепроверяем занятость группы
        (после rollback SELECT видит закоммиченного «победителя»). Так проигравший
        гонку получает нормативный `team_mail_group_taken`, а не «имя занято».
        """
        constraint = _violated_constraint(exc)
        if constraint is not None:
            return constraint == _MAIL_GROUP_CONSTRAINT
        return mail_group_id is not None and await self._teams.exists_by_mail_group_id(
            mail_group_id, exclude_id=exclude_id
        )

    async def _require_user_exists(self, user_id: uuid.UUID, *, field: str) -> None:
        """Проверяет существование пользователя-ссылки; иначе → 422 с полем."""
        if not await self._users.get_existing_ids({user_id}):
            raise unprocessable(
                "Пользователь не найден",
                details=[{"field": field, "message": "Пользователь не существует"}],
            )

    async def _validate_members(self, member_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Проверяет существование всех участников; несуществующие → 422 member_ids."""
        requested = set(member_ids)
        if not requested:
            return set()
        existing = await self._users.get_existing_ids(requested)
        if existing != requested:
            raise unprocessable(
                "Участник не найден",
                details=[{"field": "member_ids", "message": "Пользователь не существует"}],
            )
        return requested

    @staticmethod
    def _to_item(team: Team, number_count: int, mailbox_count: int) -> TeamListItem:
        """Собирает элемент ответа (лидер опционален; member_count включает лидера)."""
        return TeamListItem(
            id=team.id,
            name=team.name,
            mail_group_id=team.mail_group_id,
            leader_id=team.leader_id,
            leader_username=team.leader.username if team.leader is not None else None,
            member_count=len(team.members),
            number_count=number_count,
            mailbox_count=mailbox_count,
            members=[TeamMember(id=member.id, username=member.username) for member in team.members],
            created_at=team.created_at,
            updated_at=team.updated_at,
        )
