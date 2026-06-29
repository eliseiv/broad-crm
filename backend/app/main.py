"""Фабрика FastAPI-приложения (01-architecture.md, 05-security.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import AppEnv, Settings, get_settings
from app.db import get_sessionmaker
from app.errors import register_exception_handlers
from app.logging import configure_logging, get_logger
from app.services.provisioning_service import ProvisioningService

logger = get_logger(__name__)

_SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Старт: recovery зависших installing + регенерация file_sd из БД (ADR-006)."""
    settings = get_settings()
    provisioning = ProvisioningService(sessionmaker=get_sessionmaker(), settings=settings)
    try:
        recovered = await provisioning.recover_stuck_installing()
        regenerated = await provisioning.regenerate_file_sd()
        logger.info("startup_recovery", recovered=recovered, file_sd_regenerated=regenerated)
    except Exception as exc:
        logger.error("startup_recovery_failed", error_type=type(exc).__name__)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создаёт и конфигурирует приложение. docs_url/openapi_url зависят от APP_ENV."""
    settings = settings or get_settings()
    configure_logging(json_logs=settings.app_env is AppEnv.production)

    docs_url = "/api/docs" if settings.docs_enabled else None
    redoc_url = "/api/redoc" if settings.docs_enabled else None
    openapi_url = "/api/openapi.json" if settings.docs_enabled else None

    app = FastAPI(
        title="CRM — Мониторинг серверов",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
