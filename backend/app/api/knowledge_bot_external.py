"""Внешний write-контур регистрации запуска ИИ-бота (ADR-076).

`POST /api/external/knowledge-bot/link`: тот же `X-API-Key` / CSRF-exempt /
`Cache-Control: no-store`, что у `/api/external/documents/*`. Роутер документов
остаётся только GET (ADR-060 §3).
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import KnowledgeBotLinkServiceDep
from app.infra.documents_api_key import DocumentsApiKeyDep
from app.schemas.documents import ExternalUserAccessResponse
from app.schemas.knowledge_bot import KnowledgeBotLinkRequest

router = APIRouter(prefix="/external/knowledge-bot", tags=["knowledge-bot-external"])

_NO_STORE = "no-store"


@router.post("/link", response_model=ExternalUserAccessResponse)
async def link_knowledge_bot(
    payload: KnowledgeBotLinkRequest,
    service: KnowledgeBotLinkServiceDep,
    _key: DocumentsApiKeyDep,
    response: Response,
) -> ExternalUserAccessResponse:
    """Резолв пользователя + upsert `knowledge_bot_links`. Не найден → 404."""
    response.headers["Cache-Control"] = _NO_STORE
    return await service.link(
        telegram_user_id=payload.telegram_user_id,
        username=payload.username,
    )
