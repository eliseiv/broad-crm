"""Роутер реестра CRM-команд (04-api.md#teams, ADR-022). Гейт матрицы `teams:*`.

CRM-команды ≠ mail-«команды» (`GET /api/mail/teams`, прокси без хранения). Здесь —
uuid, лидер + участники, БД CRM. Гейт `require("teams", <action>)`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import MailServiceDep, Principal, TeamServiceDep, require
from app.schemas.mail import TeamMailboxesResponse
from app.schemas.sms import TeamNumbersResponse
from app.schemas.team import (
    TeamCreateRequest,
    TeamListItem,
    TeamListResponse,
    TeamUpdateRequest,
)

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=TeamListResponse)
async def list_teams(
    service: TeamServiceDep,
    _principal: Annotated[Principal, Depends(require("teams", "view"))],
) -> TeamListResponse:
    """Список команд (лидер, участники, member_count), сортировка created_at DESC."""
    return await service.list_teams()


@router.post("", response_model=TeamListItem, status_code=status.HTTP_201_CREATED)
async def create_team(
    payload: TeamCreateRequest,
    service: TeamServiceDep,
    _principal: Annotated[Principal, Depends(require("teams", "create"))],
) -> TeamListItem:
    """Создаёт команду (лидер → в участники; name 422/409; ссылки 422)."""
    return await service.create_team(payload)


@router.patch("/{team_id}", response_model=TeamListItem)
async def update_team(
    team_id: uuid.UUID,
    payload: TeamUpdateRequest,
    service: TeamServiceDep,
    _principal: Annotated[Principal, Depends(require("teams", "edit"))],
) -> TeamListItem:
    """Редактирование команды (name/лидер/состав); лидер всегда в составе."""
    return await service.update_team(team_id, payload)


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_team(
    team_id: uuid.UUID,
    service: TeamServiceDep,
    _principal: Annotated[Principal, Depends(require("teams", "delete"))],
) -> Response:
    """Удаляет команду (hard delete; каскад `user_teams`)."""
    await service.delete_team(team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{team_id}/numbers", response_model=TeamNumbersResponse)
async def list_team_numbers(
    team_id: uuid.UUID,
    service: TeamServiceDep,
    _principal: Annotated[Principal, Depends(require("teams", "view"))],
) -> TeamNumbersResponse:
    """SMS-номера команды для detail-панели /teams (ADR-030). Нет команды → 404."""
    return await service.list_team_numbers(team_id)


@router.get("/{team_id}/mailboxes", response_model=TeamMailboxesResponse)
async def list_team_mailboxes(
    team_id: uuid.UUID,
    service: TeamServiceDep,
    mail_service: MailServiceDep,
    _principal: Annotated[Principal, Depends(require("teams", "view"))],
) -> TeamMailboxesResponse:
    """Почты команды для detail-панели /teams (ADR-038). Нет команды → 404.

    Резолв `teams.mail_group_id`; NULL/`mail_enabled=false` → пустой список. Иначе
    прокси external /mailboxes по группе; внешний сервис недоступен → 502.
    """
    mail_group_id = await service.get_team_mail_group_id(team_id)
    return await mail_service.list_team_mailboxes(mail_group_id)
