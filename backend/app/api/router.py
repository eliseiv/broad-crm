"""Агрегирующий роутер /api."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    ai_keys,
    auth,
    backends,
    documents,
    documents_external,
    health,
    mail,
    mail_ingest,
    mail_me,
    mail_telegram,
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
api_router.include_router(mail_ingest.router)
api_router.include_router(mail_telegram.router)
api_router.include_router(mail_me.router)
api_router.include_router(permissions.router)
api_router.include_router(users.router)
api_router.include_router(roles.router)
api_router.include_router(teams.router)
api_router.include_router(sms.router)
api_router.include_router(sms_webhooks.router)
api_router.include_router(documents.router)
api_router.include_router(documents_external.router)
