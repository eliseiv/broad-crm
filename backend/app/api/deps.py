"""FastAPI-зависимости: БД, auth, фабрики сервисов."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import get_session, get_sessionmaker
from app.domain.permissions import full_catalog_permissions
from app.errors import forbidden, unauthorized
from app.infra.jwt import TokenError, decode_access_token
from app.infra.mail_client import get_mail_client
from app.infra.prometheus import get_prometheus_client
from app.infra.rate_limit import get_login_rate_limiter
from app.infra.telegram import TelegramClient
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.backend_repository import BackendRepository
from app.repositories.proxy_repository import ProxyRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.server_repository import ServerRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_repository import UserRepository
from app.services.ai_key_monitor_service import AiKeyMonitorService
from app.services.ai_key_service import AiKeyService
from app.services.auth_service import AuthService
from app.services.backend_monitor_service import BackendMonitorService
from app.services.backend_service import BackendService
from app.services.mail_service import MailService
from app.services.monitoring_service import MonitoringService
from app.services.provisioning_service import ProvisioningService
from app.services.proxy_monitor_service import ProxyMonitorService
from app.services.proxy_service import ProxyService
from app.services.role_service import RoleService
from app.services.server_service import ServerService
from app.services.team_service import TeamService
from app.services.user_service import UserService

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep() -> Settings:
    """Настройки приложения."""
    return get_settings()


DbSession = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


@dataclass(frozen=True)
class Principal:
    """Аутентифицированный принципал с актуальными правами (ADR-021, enforcement).

    Права загружаются из БД на каждый запрос: правки роли применяются без пере-логина.
    Супер-админ (`.env`) — `is_superadmin=True`, `permissions` = полный каталог.
    """

    username: str
    role: str
    permissions: dict[str, list[str]]
    is_superadmin: bool


async def get_current_principal(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Декодит JWT и формирует принципал (свежая загрузка прав из БД, ADR-021).

    Супер-админ → полный каталог. БД-пользователь → права роли по `uid`; если
    пользователь не найден ИЛИ `is_active=false` → 401 (JWT аннулируется без
    пере-логина). Легаси-токен (без role/superadmin/uid) → 401.
    """
    if credentials is None or not credentials.credentials:
        raise unauthorized()
    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as exc:
        raise unauthorized() from exc

    if claims.superadmin:
        return Principal(
            username=claims.sub,
            role="admin",
            permissions=full_catalog_permissions(),
            is_superadmin=True,
        )

    if claims.uid is None:
        raise unauthorized()
    try:
        user_id = uuid.UUID(claims.uid)
    except ValueError as exc:
        raise unauthorized() from exc

    user = await UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise unauthorized()

    return Principal(
        username=user.username,
        role=user.role.name,
        permissions=dict(user.role.permissions),
        is_superadmin=False,
    )


PrincipalDep = Annotated[Principal, Depends(get_current_principal)]


def require(page: str, action: str) -> Callable[[Principal], Awaitable[Principal]]:
    """Фабрика зависимости RBAC: пропускает супер-админа или `action ∈ perms[page]`.

    Иначе → 403 forbidden. Применяется ко всем ресурсным эндпоинтам вместо прежнего
    «любой аутентифицированный» (маппинг метод→действие — 04-api.md#rbac-и-enforcement-прав).
    """

    async def _require(principal: PrincipalDep) -> Principal:
        if principal.is_superadmin or action in principal.permissions.get(page, []):
            return principal
        raise forbidden()

    return _require


async def require_admin(principal: PrincipalDep) -> Principal:
    """Гейт Users/Roles/Permissions API: супер-админ ИЛИ роль `admin`, иначе 403."""
    if principal.is_superadmin or principal.role == "admin":
        return principal
    raise forbidden()


RequireAdmin = Annotated[Principal, Depends(require_admin)]


def get_auth_service(session: DbSession, settings: SettingsDep) -> AuthService:
    """Сервис аутентификации (супер-админ из .env ИЛИ БД-пользователь)."""
    return AuthService(
        settings=settings,
        rate_limiter=get_login_rate_limiter(),
        user_repository=UserRepository(session),
    )


def get_user_service(session: DbSession) -> UserService:
    """Сервис реестра пользователей (require_admin, ADR-021/022)."""
    return UserService(
        users=UserRepository(session),
        roles=RoleRepository(session),
        teams=TeamRepository(session),
    )


def get_role_service(session: DbSession) -> RoleService:
    """Сервис реестра ролей (матрица roles:*, ADR-022)."""
    return RoleService(repository=RoleRepository(session))


def get_team_service(session: DbSession) -> TeamService:
    """Сервис реестра CRM-команд (матрица teams:*, ADR-022)."""
    return TeamService(
        teams=TeamRepository(session),
        users=UserRepository(session),
    )


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


def get_backend_monitor(settings: SettingsDep) -> BackendMonitorService:
    """Монитор бэков для немедленной проверки при create/edit (собственный
    sessionmaker для фоновой задачи). Telegram — только при notifier_enabled."""
    telegram = (
        TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
        if settings.notifier_enabled
        else None
    )
    return BackendMonitorService(
        sessionmaker=get_sessionmaker(), telegram=telegram, settings=settings
    )


def get_backend_service(
    session: DbSession,
    monitor: Annotated[BackendMonitorService, Depends(get_backend_monitor)],
) -> BackendService:
    """Сервис реестра бэков."""
    return BackendService(repository=BackendRepository(session), monitor=monitor)


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
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
RoleServiceDep = Annotated[RoleService, Depends(get_role_service)]
TeamServiceDep = Annotated[TeamService, Depends(get_team_service)]
ServerServiceDep = Annotated[ServerService, Depends(get_server_service)]
AiKeyServiceDep = Annotated[AiKeyService, Depends(get_ai_key_service)]
ProxyServiceDep = Annotated[ProxyService, Depends(get_proxy_service)]
BackendServiceDep = Annotated[BackendService, Depends(get_backend_service)]
MailServiceDep = Annotated[MailService, Depends(get_mail_service)]
ClientIp = Annotated[str, Depends(get_client_ip)]
