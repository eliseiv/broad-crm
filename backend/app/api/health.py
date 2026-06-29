"""Health-эндпоинт (04-api.md#health). Без JWT."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.infra.prometheus import PrometheusUnavailable, get_prometheus_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: DbSession) -> dict[str, Any]:
    """Liveness/readiness; деградация зависимостей не роняет статус на 5xx."""
    db_status = "up"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"

    prom_status = "up"
    try:
        await get_prometheus_client().query("vector(1)")
    except PrometheusUnavailable:
        prom_status = "down"

    overall = "ok" if db_status == "up" and prom_status == "up" else "degraded"
    return {"status": overall, "db": db_status, "prometheus": prom_status}
