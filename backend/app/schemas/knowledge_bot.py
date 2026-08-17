"""Схемы внешнего write-контура ИИ-бота (04-api.md#external-knowledge-bot, ADR-076)."""

from __future__ import annotations

from pydantic import BaseModel


class KnowledgeBotLinkRequest(BaseModel):
    """Тело POST /api/external/knowledge-bot/link."""

    telegram_user_id: int
    username: str | None = None
