"""Бизнес-логика реестра CRM-команд (modules/teams, 04-api.md#teams, ADR-022).

Инвариант «лидер ∈ участники» обеспечивается на всех путях записи (create + update, в
т.ч. при смене лидера). Существование `leader_id`/`member_ids` (пользователи) валидирует
`UserRepository` → 422 с указанием поля. Уникальность `name` → 409 team_name_taken.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.domain.identity import IdentityNameError, validate_identity_name
from app.errors import team_name_taken, team_not_found, unprocessable
from app.logging import get_logger
from app.models.team import Team
from app.repositories.team_repository import TeamRepository
from app.repositories.user_repository import UserRepository
from app.schemas.team import (
    TeamCreateRequest,
    TeamListItem,
    TeamListResponse,
    TeamMember,
    TeamUpdateRequest,
)

logger = get_logger(__name__)


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
    """CRUD реестра CRM-команд с инвариантом «лидер ∈ участники» и валидацией ссылок."""

    def __init__(self, *, teams: TeamRepository, users: UserRepository) -> None:
        self._teams = teams
        self._users = users

    async def list_teams(self) -> TeamListResponse:
        """Список команд (created_at DESC, id) с лидером и участниками."""
        teams = await self._teams.list_all()
        return TeamListResponse(items=[self._to_item(team) for team in teams])

    async def create_team(self, payload: TeamCreateRequest) -> TeamListItem:
        """Создаёт команду. Прецеденция: name-формат (422) → существование
        leader_id/member_ids (422) → уникальность name (409). Лидер — в участники."""
        name = _validate_name(payload.name)

        await self._require_user_exists(payload.leader_id, field="leader_id")
        member_set = await self._validate_members(payload.member_ids)

        if await self._teams.exists_by_name(name):
            raise team_name_taken()

        try:
            team = await self._teams.create(
                name=name, leader_id=payload.leader_id, member_ids=member_set
            )
            await self._teams.session.commit()
        except IntegrityError as exc:
            await self._teams.session.rollback()
            logger.info("team_create_conflict", name=name)
            raise team_name_taken() from exc

        reloaded = await self._teams.get_with_members(team.id)
        assert reloaded is not None  # только что создана в этой сессии
        logger.info("team_created", team_id=str(team.id))
        return self._to_item(reloaded)

    async def update_team(self, team_id: uuid.UUID, payload: TeamUpdateRequest) -> TeamListItem:
        """Редактирует команду. Прецеденция: 404 → name-формат (422) → существование
        leader_id/member_ids (422) → уникальность name (409). Лидер всегда в составе."""
        team = await self._teams.get_with_members(team_id)
        if team is None:
            raise team_not_found()

        fields_set = payload.model_fields_set

        new_name: str | None = None
        if "name" in fields_set and payload.name is not None:
            new_name = _validate_name(payload.name)

        leader_provided = "leader_id" in fields_set and payload.leader_id is not None
        if leader_provided:
            assert payload.leader_id is not None
            await self._require_user_exists(payload.leader_id, field="leader_id")

        members_provided = "member_ids" in fields_set and payload.member_ids is not None
        member_set: set[uuid.UUID] | None = None
        if members_provided:
            assert payload.member_ids is not None
            member_set = await self._validate_members(payload.member_ids)

        if new_name is not None and new_name != team.name:
            if await self._teams.exists_by_name(new_name, exclude_id=team.id):
                raise team_name_taken()
            team.name = new_name

        old_leader_id = team.leader_id
        effective_leader = payload.leader_id if leader_provided else old_leader_id
        assert effective_leader is not None
        if leader_provided:
            team.leader_id = effective_leader

        # Инвариант «лидер ∈ участники» + семантика замены/сохранения состава.
        leader_changed = leader_provided and effective_leader != old_leader_id
        if member_set is not None:
            await self._teams.replace_members(team.id, member_set | {effective_leader})
        elif leader_changed:
            current = {member.id for member in team.members}
            await self._teams.replace_members(team.id, current | {effective_leader})

        try:
            await self._teams.session.commit()
        except IntegrityError as exc:
            await self._teams.session.rollback()
            logger.info("team_update_conflict", team_id=str(team_id))
            raise team_name_taken() from exc

        reloaded = await self._teams.get_with_members(team_id)
        assert reloaded is not None  # существует (только что обновлена)
        logger.info("team_updated", team_id=str(team_id))
        return self._to_item(reloaded)

    async def delete_team(self, team_id: uuid.UUID) -> None:
        """Hard-delete (каскад `user_teams`); повтор → 404 team_not_found."""
        deleted = await self._teams.delete_by_id(team_id)
        if not deleted:
            raise team_not_found()
        await self._teams.session.commit()
        logger.info("team_deleted", team_id=str(team_id))

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
    def _to_item(team: Team) -> TeamListItem:
        """Собирает элемент ответа (лидер + участники, member_count включает лидера)."""
        return TeamListItem(
            id=team.id,
            name=team.name,
            leader_id=team.leader_id,
            leader_username=team.leader.username,
            member_count=len(team.members),
            members=[TeamMember(id=member.id, username=member.username) for member in team.members],
            created_at=team.created_at,
            updated_at=team.updated_at,
        )
