"""Агрегирующий роутер /api."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import ai_keys, auth, health, servers

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(servers.router)
api_router.include_router(ai_keys.router)
