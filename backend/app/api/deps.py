"""FastAPI-зависимости: БД, auth, фабрики сервисов."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session, get_sessionmaker
from app.errors import unauthorized
from app.infra.jwt import TokenError, decode_access_token
from app.infra.mail_client import get_mail_client
from app.infra.prometheus import get_prometheus_client
from app.infra.rate_limit import get_login_rate_limiter
from app.infra.telegram import TelegramClient
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.proxy_repository import ProxyRepository
from app.repositories.server_repository import ServerRepository
from app.services.ai_key_monitor_service import AiKeyMonitorService
from app.services.ai_key_service import AiKeyService
from app.services.auth_service import AuthService
from app.services.mail_service import MailService
from app.services.monitoring_service import MonitoringService
from app.services.provisioning_service import ProvisioningService
from app.services.proxy_monitor_service import ProxyMonitorService
from app.services.proxy_service import ProxyService
from app.services.server_service import ServerService

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep() -> Settings:
    """Настройки приложения."""
    return get_settings()


DbSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """Валидирует Bearer-JWT, возвращает username. Иначе 401 unauthorized."""
    if credentials is None or not credentials.credentials:
        raise unauthorized()
    try:
        return decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise unauthorized() from exc


CurrentUser = Annotated[str, Depends(get_current_user)]


def get_auth_service(settings: SettingsDep) -> AuthService:
    """Сервис аутентификации."""
    return AuthService(settings=settings, rate_limiter=get_login_rate_limiter())


def get_provisioning_service(settings: SettingsDep) -> ProvisioningService:
    """Сервис провижининга (собственный sessionmaker для фоновых задач)."""
    return ProvisioningService(sessionmaker=get_sessionmaker(), settings=settings)


def get_monitoring_service() -> MonitoringService:
    """Сервис мониторинга (клиент Prometheus)."""
    return MonitoringService(client=get_prometheus_client())


def get_server_service(
    session: DbSession,
    monitoring: Annotated[MonitoringService, Depends(get_monitoring_service)],
    provisioning: Annotated[ProvisioningService, Depends(get_provisioning_service)],
) -> ServerService:
    """Сервис реестра серверов."""
    return ServerService(
        repository=ServerRepository(session),
        monitoring=monitoring,
        provisioning=provisioning,
    )


def get_ai_key_monitor(settings: SettingsDep) -> AiKeyMonitorService:
    """Монитор AI-ключей для немедленной проверки при создании (собственный
    sessionmaker для фоновой задачи). Telegram — только при notifier_enabled."""
    telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.notifier_enabled
        else None
    )
    return AiKeyMonitorService(
        sessionmaker=get_sessionmaker(), telegram=telegram, settings=settings
    )


def get_ai_key_service(
    session: DbSession,
    monitor: Annotated[AiKeyMonitorService, Depends(get_ai_key_monitor)],
) -> AiKeyService:
    """Сервис реестра AI-ключей."""
    return AiKeyService(repository=AiKeyRepository(session), monitor=monitor)


def get_proxy_monitor(settings: SettingsDep) -> ProxyMonitorService:
    """Монитор прокси для немедленной проверки при create/edit (собственный
    sessionmaker для фоновой задачи). Telegram — только при notifier_enabled."""
    telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.notifier_enabled
        else None
    )
    return ProxyMonitorService(
        sessionmaker=get_sessionmaker(), telegram=telegram, settings=settings
    )


def get_proxy_service(
    session: DbSession,
    monitor: Annotated[ProxyMonitorService, Depends(get_proxy_monitor)],
) -> ProxyService:
    """Сервис реестра прокси."""
    return ProxyService(repository=ProxyRepository(session), monitor=monitor)


def get_mail_service(settings: SettingsDep) -> MailService:
    """Сервис почты (read-through-прокси к postapp.store; клиент из настроек)."""
    return MailService(client=get_mail_client(), settings=settings)


def get_client_ip(request: Request) -> str:
    """Реальный IP клиента для rate-limit входа (05-security.md).

    За reverse-proxy (nginx) request.client.host = IP прокси, поэтому все клиенты
    попали бы в один rate-limit bucket. Берём реальный IP из заголовков прокси
    (X-Real-IP → первый из X-Forwarded-For) с fallback на прямое подключение.
    nginx обязан проставлять `proxy_set_header X-Real-IP $remote_addr` (devops).
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first

    if request.client is not None:
        return request.client.host
    return "unknown"


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ServerServiceDep = Annotated[ServerService, Depends(get_server_service)]
AiKeyServiceDep = Annotated[AiKeyService, Depends(get_ai_key_service)]
ProxyServiceDep = Annotated[ProxyService, Depends(get_proxy_service)]
MailServiceDep = Annotated[MailService, Depends(get_mail_service)]
ClientIp = Annotated[str, Depends(get_client_ip)]
