"""Роутер рассылки (04-api.md#broadcast, ADR-076). Гейт матрицы `broadcast:*`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import BroadcastServiceDep, Principal, require
from app.schemas.broadcast import (
    BroadcastAudienceResponse,
    BroadcastCreateRequest,
    BroadcastSendResponse,
)

router = APIRouter(prefix="/broadcasts", tags=["broadcast"])

ViewDep = Annotated[Principal, Depends(require("broadcast", "view"))]
SendDep = Annotated[Principal, Depends(require("broadcast", "send"))]


@router.get("/audience", response_model=BroadcastAudienceResponse)
async def get_audience(
    service: BroadcastServiceDep,
    _principal: ViewDep,
) -> BroadcastAudienceResponse:
    """Роли для чекбоксов + счётчики запуска ИИ-бота."""
    return await service.get_audience()


@router.post("", response_model=BroadcastSendResponse)
async def send_broadcast(
    payload: BroadcastCreateRequest,
    service: BroadcastServiceDep,
    _principal: SendDep,
) -> BroadcastSendResponse:
    """Fan-out текста. Пустой `KNOWLEDGE_BOT_TOKEN` → 503; частичный успех → 200."""
    return await service.send(payload)
