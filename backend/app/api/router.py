"""Агрегирующий роутер /api."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    ai_keys,
    auth,
    backends,
    health,
    mail,
    permissions,
    proxies,
    roles,
    servers,
    sms,
    sms_webhooks,
    teams,
    users,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(servers.router)
api_router.include_router(ai_keys.router)
api_router.include_router(proxies.router)
api_router.include_router(backends.router)
api_router.include_router(mail.router)
api_router.include_router(permissions.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(teams.router)
api_router.include_router(sms.router)
api_router.include_router(sms_webhooks.router)
