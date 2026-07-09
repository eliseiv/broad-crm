"""Фабрика FastAPI-приложения (01-architecture.md, 05-security.md)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import AppEnv, Settings, get_settings
from app.db import get_sessionmaker
from app.errors import register_exception_handlers
from app.infra.prometheus import get_prometheus_client
from app.infra.sms_telegram import SmsBotClient
from app.infra.telegram import TelegramClient
from app.logging import configure_logging, get_logger
from app.services.ai_key_monitor_service import AiKeyMonitorService
from app.services.backend_monitor_service import BackendMonitorService
from app.services.monitoring_service import MonitoringService
from app.services.notifier_service import NotifierService
from app.services.provisioning_service import ProvisioningService
from app.services.proxy_monitor_service import ProxyMonitorService
from app.services.sms_delivery_monitor_service import SmsDeliveryMonitorService

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

    # Telegram-нотификатор (modules/notifier, ADR-009): фоновая задача только
    # если заданы обе переменные TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID.
    notifier_task: asyncio.Task[None] | None = None
    if settings.notifier_enabled:
        notifier = NotifierService(
            telegram=TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id),
            monitoring=MonitoringService(get_prometheus_client()),
            poll_interval_sec=settings.notifier_poll_interval_sec,
            metric_window_sec=settings.notifier_metric_window_effective_sec,
        )
        notifier_task = asyncio.create_task(notifier.run())
    else:
        logger.info("notifier_disabled")

    # Монитор AI-ключей (modules/ai-keys, ADR-010): стартует ВСЕГДА (не гейтится
    # Telegram) — обновление check_status для UI работает независимо от бота.
    # Telegram-клиент передаётся только при notifier_enabled.
    ai_key_telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.notifier_enabled
        else None
    )
    ai_key_monitor = AiKeyMonitorService(
        sessionmaker=get_sessionmaker(),
        telegram=ai_key_telegram,
        settings=settings,
    )
    ai_key_monitor_task = asyncio.create_task(ai_key_monitor.run())

    # Монитор доступности прокси (modules/proxies, ADR-019): стартует ВСЕГДА
    # (не гейтится Telegram) — check_status для UI работает независимо от бота.
    # Telegram-клиент передаётся только при notifier_enabled.
    proxy_telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.notifier_enabled
        else None
    )
    proxy_monitor = ProxyMonitorService(
        sessionmaker=get_sessionmaker(),
        telegram=proxy_telegram,
        settings=settings,
    )
    proxy_monitor_task = asyncio.create_task(proxy_monitor.run())

    # Монитор доступности бэков (modules/backends, ADR-020): стартует ВСЕГДА
    # (не гейтится Telegram) — check_status для UI работает независимо от бота.
    # Telegram-клиент передаётся только при notifier_enabled.
    backend_telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.notifier_enabled
        else None
    )
    backend_monitor = BackendMonitorService(
        sessionmaker=get_sessionmaker(),
        telegram=backend_telegram,
        settings=settings,
    )
    backend_monitor_task = asyncio.create_task(backend_monitor.run())

    # Retry-монитор доставок SMS (modules/sms, ADR-030): стартует ТОЛЬКО при
    # sms_bot_enabled (задан SMS_TELEGRAM_BOT_TOKEN). Переотправляет pending/failed
    # доставки; при отключённом боте доставка не работает и монитор не нужен.
    sms_delivery_monitor_task: asyncio.Task[None] | None = None
    if settings.sms_bot_enabled:
        sms_delivery_monitor = SmsDeliveryMonitorService(
            sessionmaker=get_sessionmaker(),
            bot=SmsBotClient(settings.sms_telegram_bot_token, settings.sms_telegram_proxy_url),
            settings=settings,
        )
        sms_delivery_monitor_task = asyncio.create_task(sms_delivery_monitor.run())
    else:
        logger.info("sms_delivery_monitor_disabled")

    yield

    if notifier_task is not None:
        notifier_task.cancel()
        with suppress(asyncio.CancelledError):
            await notifier_task

    ai_key_monitor_task.cancel()
    with suppress(asyncio.CancelledError):
        await ai_key_monitor_task

    proxy_monitor_task.cancel()
    with suppress(asyncio.CancelledError):
        await proxy_monitor_task

    backend_monitor_task.cancel()
    with suppress(asyncio.CancelledError):
        await backend_monitor_task

    if sms_delivery_monitor_task is not None:
        sms_delivery_monitor_task.cancel()
        with suppress(asyncio.CancelledError):
            await sms_delivery_monitor_task


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создаёт и конфигурирует приложение. docs_url/openapi_url зависят от APP_ENV."""
    settings = settings or get_settings()
    configure_logging(json_logs=settings.app_env is AppEnv.production)

    docs_url = "/api/docs" if settings.docs_enabled else None
    redoc_url = "/api/redoc" if settings.docs_enabled else None
    openapi_url = "/api/openapi.json" if settings.docs_enabled else None

    app = FastAPI(
        title="CRM",
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
