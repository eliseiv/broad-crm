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
from app.domain.channels import CHANNEL_MAIL, CHANNEL_SMS, Channel
from app.domain.documents import DocumentScope
from app.domain.mail import MailScope
from app.domain.permissions import full_catalog_permissions, permissions_subset
from app.domain.sms import SmsScope
from app.domain.superadmin import SUPERADMIN_USER_ID
from app.errors import forbidden, unauthorized
from app.infra.jwt import SetupTokenClaims, TokenError, decode_access_token, decode_setup_token
from app.infra.mail_client import get_mail_client, get_mail_server_client
from app.infra.prometheus import get_prometheus_client
from app.infra.rate_limit import get_login_rate_limiter
from app.infra.sms_telegram import SmsBotClient
from app.infra.telegram import TelegramClient
from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.backend_repository import BackendRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.mail_account_repository import MailAccountRepository
from app.repositories.proxy_repository import ProxyRepository
from app.repositories.role_repository import RoleRepository
from app.repositories.server_repository import ServerRepository
from app.repositories.sms_number_repository import SmsNumberRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_channel_team_repository import UserChannelTeamRepository
from app.repositories.user_repository import UserRepository
from app.schemas.user import TeamRef
from app.services.ai_key_monitor_service import AiKeyMonitorService
from app.services.ai_key_service import AiKeyService
from app.services.auth_service import AuthService
from app.services.backend_monitor_service import BackendMonitorService
from app.services.backend_service import BackendService
from app.services.document_service import DocumentService
from app.services.mail_ingest_service import MailIngestService
from app.services.mail_service import MailService
from app.services.mail_telegram_service import MailTelegramService
from app.services.monitoring_service import MonitoringService
from app.services.provisioning_service import ProvisioningService
from app.services.proxy_monitor_service import ProxyMonitorService
from app.services.proxy_service import ProxyService
from app.services.role_service import RoleService
from app.services.server_service import ServerService
from app.services.sms_ingest_service import SmsIngestService
from app.services.sms_message_service import SmsMessageService
from app.services.sms_number_service import SmsNumberService
from app.services.sms_sync_service import SmsSyncService
from app.services.sms_telegram_link_service import SmsTelegramLinkService
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
    # Идентичность принципала — НЕ-опциональна (ADR-051 §1.2): принципала без `user_id`
    # больше не существует. БД-пользователь → claim `uid`; супер-админ (`.env`) →
    # константа `SUPERADMIN_USER_ID` (системная строка-якорь), БЕЗ запроса в БД
    # (fallback-инвариант ADR-008 сохранён). Снятие `| None` из ТИПА — гарант: mypy не даст
    # будущей персональной фиче снова споткнуться о «принципала без идентичности».
    # Нужен для scope видимости SMS/почты, привязки Telegram и личного состояния
    # (05-security.md#расширение-principal, ADR-030 §6, ADR-050/051).
    user_id: uuid.UUID
    # Флаги «Без команды» по каналам (ADR-055 §2.2/§3) — `users.<channel>_includes_unassigned`.
    # Загружаются той же строкой пользователя, что и права (лишнего round-trip'а нет);
    # у супер-админа не используются (`sees_all_teams=True` перекрывает их — §3).
    mail_includes_unassigned: bool = False
    sms_includes_unassigned: bool = False
    # Единственная роль пользователя (`users.role_id`, ADR-021) — для per-node фильтра
    # видимости документов (ADR-059): узел виден ⇔ публичен ИЛИ его эффективный набор
    # ролей содержит `role_id`. Берётся из той же уже загруженной строки (лишнего запроса
    # нет). У консольного супер-админа роли-строки в `users` нет → `None` (его видимость
    # покрывает admin-предикат `sees_all`, роль не нужна).
    role_id: uuid.UUID | None = None


async def get_current_principal(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    """Декодит JWT и формирует принципал (свежая загрузка прав из БД, ADR-021).

    Супер-админ → полный каталог + `user_id = SUPERADMIN_USER_ID` (константа якоря,
    ADR-051 §1.2: ни одного запроса в БД, JWT не меняется — `uid` супер-админу в токен
    не кладётся, уже выпущенные токены продолжают работать). БД-пользователь → права
    роли по `uid`; если пользователь не найден ИЛИ `is_active=false` → 401 (JWT
    аннулируется без пере-логина). Легаси-токен (без role/superadmin/uid) → 401.
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
            user_id=SUPERADMIN_USER_ID,
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
        user_id=user.id,
        mail_includes_unassigned=user.mail_includes_unassigned,
        sms_includes_unassigned=user.sms_includes_unassigned,
        role_id=user.role_id,
    )


PrincipalDep = Annotated[Principal, Depends(get_current_principal)]


async def get_setup_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> SetupTokenClaims:
    """Декодит limited-scope setup-токен (`type:"pwd_setup"`) для set-password (ADR-025).

    Отдельная зависимость: принимает ТОЛЬКО setup-token (обычный access-token отвергается
    внутри `decode_setup_token`). Нет/просрочен/неверный тип → 401 unauthorized.
    """
    if credentials is None or not credentials.credentials:
        raise unauthorized()
    try:
        return decode_setup_token(credentials.credentials)
    except TokenError as exc:
        raise unauthorized() from exc


SetupPrincipalDep = Annotated[SetupTokenClaims, Depends(get_setup_principal)]


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
    """Сервис реестра пользователей (require_admin, ADR-021/022; доп-команды — ADR-055 §5.2)."""
    return UserService(
        users=UserRepository(session),
        roles=RoleRepository(session),
        teams=TeamRepository(session),
        channels=UserChannelTeamRepository(session),
    )


def get_role_service(session: DbSession) -> RoleService:
    """Сервис реестра ролей (матрица roles:*, ADR-022)."""
    return RoleService(repository=RoleRepository(session))


def get_team_service(session: DbSession) -> TeamService:
    """Сервис реестра CRM-команд (матрица teams:*, ADR-022; number_count/numbers ADR-030;
    mailbox_count — ADR-048 §1, гейт тот же `teams:view`, MailScope не применяется §4).

    `channels` — нормализация per-channel добавок при правке состава команды (ADR-055
    §2.3, путь 2): ставший участником теряет эту команду как добавку канала.
    """
    return TeamService(
        teams=TeamRepository(session),
        users=UserRepository(session),
        numbers=SmsNumberRepository(session),
        mailboxes=MailAccountRepository(session),
        channels=UserChannelTeamRepository(session),
    )


def principal_sees_all_sms_teams(principal: Principal) -> bool:
    """Admin-уровень видимости SMS ⇔ супер-админ ИЛИ полный каталог прав (ADR-032/036).

    Единый предикат-источник истины: используется и SMS-scope (`get_sms_scope`), и
    производным флагом `sees_all_sms_teams` в `GET /api/auth/me` (ADR-036). Признак
    устойчив к переименованию роли (не завязан на имя) и не требует нового права.
    """
    return principal.is_superadmin or permissions_subset(
        full_catalog_permissions(), principal.permissions
    )


async def get_sms_scope(principal: PrincipalDep, session: DbSession) -> SmsScope:
    """Фабрика scope: «видит все команды» ⇔ супер-админ ИЛИ полный каталог прав (ADR-032).

    Предикат — `principal_sees_all_sms_teams` (общий с `GET /api/auth/me`, ADR-055 §3 его
    НЕ менял). При полном каталоге прав роль считается admin-уровнем и видит SMS всех
    команд, включая бесхозные ⇒ `includes_unassigned=True` (остальные поля не используются).

    Иначе (ADR-055 §3): `team_ids` = `user_teams` ∪ доп-команды канала `sms` (одним
    UNION-запросом), `includes_unassigned` = `users.sms_includes_unassigned` (уже загружен
    в принципал). Нет команд и нет флага → пустой scope (анти-энумерация).
    """
    sees_all_teams = principal_sees_all_sms_teams(principal)
    if sees_all_teams:
        return SmsScope(sees_all_teams=True, team_ids=frozenset(), includes_unassigned=True)
    team_ids = await UserChannelTeamRepository(session).scope_team_ids(
        principal.user_id, CHANNEL_SMS
    )
    return SmsScope(
        sees_all_teams=False,
        team_ids=team_ids,
        includes_unassigned=principal.sms_includes_unassigned,
    )


def principal_sees_all_mail_teams(principal: Principal) -> bool:
    """Admin-уровень видимости почты ⇔ супер-админ ИЛИ полный каталог прав (ADR-038 §3).

    Единый предикат-источник истины: используется и mail-scope (`get_mail_scope`), и
    производным флагом `sees_all_mail_teams` в `GET /api/auth/me`. Симметрично
    `principal_sees_all_sms_teams`; устойчив к переименованию роли, без нового права.
    """
    return principal.is_superadmin or permissions_subset(
        full_catalog_permissions(), principal.permissions
    )


async def get_mail_scope(principal: PrincipalDep, session: DbSession) -> MailScope:
    """Фабрика scope почты (ADR-044 §7 в редакции ADR-055 §3, образец `get_sms_scope`).

    «Видит все команды» ⇔ супер-админ ИЛИ полный каталог прав (предикат не изменён); такой
    актор видит и бесхозные ящики ⇒ `includes_unassigned=True`.

    Иначе (ADR-055 §3): `team_ids` = `user_teams` ∪ доп-команды канала `mail` (одним
    UNION-запросом), `includes_unassigned` = `users.mail_includes_unassigned`. Нет команд и
    нет флага → пустая видимость (анти-энумерация, выборка не выполняется).
    """
    sees_all_teams = principal_sees_all_mail_teams(principal)
    if sees_all_teams:
        return MailScope(sees_all_teams=True, team_ids=frozenset(), includes_unassigned=True)
    team_ids = await UserChannelTeamRepository(session).scope_team_ids(
        principal.user_id, CHANNEL_MAIL
    )
    return MailScope(
        sees_all_teams=False,
        team_ids=team_ids,
        includes_unassigned=principal.mail_includes_unassigned,
    )


async def resolve_channel_scope(
    session: AsyncSession, principal: Principal, channel: Channel
) -> tuple[list[TeamRef], bool]:
    """Команды канала + флаг «Без команды» для `GET /api/auth/me` (ADR-055 §5.1).

    Возвращает `(mail_teams|sms_teams, <channel>_includes_unassigned)`:

    - **admin-уровень** (`sees_all_<channel>_teams=true`): **ВСЕ команды системы** (его
      scope не сужен) и флаг **`true`** (admin видит и бесхозные). `[]` НЕ отдаётся —
      иначе фильтр «Команда» в Mini App у admin-уровня остался бы без опций (`GET
      /api/teams` там запрещён, §6.2);
    - **не-админ:** ЭФФЕКТИВНЫЙ scope канала (`user_teams` ∪ добавка), НЕ только добавка;
      флаг — из `users.<channel>_includes_unassigned`.

    Сортировка — по `name` (ru, ci): `casefold()` — регистро- и регистр-нечувствительное
    сравнение, корректное для кириллицы.
    """
    sees_all = (
        principal_sees_all_mail_teams(principal)
        if channel == CHANNEL_MAIL
        else principal_sees_all_sms_teams(principal)
    )
    includes_unassigned = (
        principal.mail_includes_unassigned
        if channel == CHANNEL_MAIL
        else principal.sms_includes_unassigned
    )
    if sees_all:
        teams = await TeamRepository(session).list_refs()
        includes_unassigned = True
    else:
        teams = await UserChannelTeamRepository(session).effective_teams(principal.user_id, channel)
    refs = [TeamRef(id=team.id, name=team.name) for team in teams]
    refs.sort(key=lambda ref: ref.name.casefold())
    return refs, includes_unassigned


def principal_sees_all_documents(principal: Principal) -> bool:
    """Admin-уровень видимости документов ⇔ супер-админ ИЛИ полный каталог прав (ADR-059).

    Тот же admin-предикат, что «видит все SMS/почты» (ADR-032); нового права не вводится,
    устойчив к переименованию роли. Такой актор видит и правит любой узел (per-role фильтр
    не применяется).
    """
    return principal.is_superadmin or permissions_subset(
        full_catalog_permissions(), principal.permissions
    )


def get_document_scope(principal: PrincipalDep) -> DocumentScope:
    """Фабрика scope видимости документов (ADR-059): `sees_all` + `role_id` пользователя.

    `role_id` уже в принципале (загружен той же строкой пользователя — без доп. запроса);
    у admin-уровня не используется. Синхронная — БД не требуется (в отличие от SMS/почты,
    где scope — множество команд).
    """
    return DocumentScope(
        sees_all=principal_sees_all_documents(principal),
        role_id=principal.role_id,
    )


def get_document_service(session: DbSession, settings: SettingsDep) -> DocumentService:
    """Сервис модуля «Документы» (CRUD дерева, резолюция видимости, copy, soft-delete)."""
    return DocumentService(
        repository=DocumentRepository(session),
        roles=RoleRepository(session),
        settings=settings,
    )


def get_sms_message_service(session: DbSession) -> SmsMessageService:
    """Сервис ленты входящих SMS (require sms:view + scope)."""
    return SmsMessageService(session)


def get_sms_number_service(session: DbSession) -> SmsNumberService:
    """Сервис реестра SMS-номеров (require sms:view/edit/transfer/delete + scope)."""
    return SmsNumberService(
        numbers=SmsNumberRepository(session),
        teams=TeamRepository(session),
    )


def get_sms_sync_service(session: DbSession, settings: SettingsDep) -> SmsSyncService:
    """Сервис синхронизации номеров из Twilio (require sms:sync)."""
    return SmsSyncService(numbers=SmsNumberRepository(session), settings=settings)


def get_sms_telegram_link_service(
    session: DbSession, settings: SettingsDep
) -> SmsTelegramLinkService:
    """Сервис Telegram-привязки оператора (link — JWT; auth — публичный)."""
    return SmsTelegramLinkService(session=session, settings=settings)


def get_sms_ingest_service(session: DbSession, settings: SettingsDep) -> SmsIngestService:
    """Сервис приёма/fan-out входящих SMS (публичный Twilio-webhook)."""
    bot = SmsBotClient(settings.sms_telegram_bot_token, settings.sms_telegram_proxy_url)
    return SmsIngestService(session, bot)


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
        backends=BackendRepository(session),
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
    return AiKeyService(
        repository=AiKeyRepository(session),
        monitor=monitor,
        backends=BackendRepository(session),
    )


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


def get_mail_service(session: DbSession, settings: SettingsDep) -> MailService:
    """Сервис почты (ADR-044): чтение из БД CRM + транзит операций ящика/reply в агрегатор.

    Два клиента агрегатора по категории пути (ADR-053 §1.1/§1.3 п.6): быстрый
    (`delete`/`sync`/`oauth-authorize`) и mail-server (`test`/`create`/`patch`/`reply` —
    агрегатор идёт на удалённый IMAP/SMTP). Категорию выбирает сервис.
    """
    return MailService(
        session=session,
        client=get_mail_client(),
        settings=settings,
        mail_server_client=get_mail_server_client(),
    )


def get_mail_ingest_service(session: DbSession, settings: SettingsDep) -> MailIngestService:
    """Сервис приёма push'а агрегатор→CRM (письма + статус ящика; HMAC, без JWT)."""
    return MailIngestService(session, max_batch=settings.mail_ingest_max_batch)


def get_mail_telegram_service(session: DbSession, settings: SettingsDep) -> MailTelegramService:
    """Сервис Telegram-слоя почты (Mini App SSO / webhook / callback / opt-out, ADR-044)."""
    return MailTelegramService(session=session, settings=settings)


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
MailIngestServiceDep = Annotated[MailIngestService, Depends(get_mail_ingest_service)]
MailTelegramServiceDep = Annotated[MailTelegramService, Depends(get_mail_telegram_service)]
MailScopeDep = Annotated[MailScope, Depends(get_mail_scope)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
DocumentScopeDep = Annotated[DocumentScope, Depends(get_document_scope)]
ClientIp = Annotated[str, Depends(get_client_ip)]
SmsScopeDep = Annotated[SmsScope, Depends(get_sms_scope)]
SmsMessageServiceDep = Annotated[SmsMessageService, Depends(get_sms_message_service)]
SmsNumberServiceDep = Annotated[SmsNumberService, Depends(get_sms_number_service)]
SmsSyncServiceDep = Annotated[SmsSyncService, Depends(get_sms_sync_service)]
SmsTelegramLinkServiceDep = Annotated[
    SmsTelegramLinkService, Depends(get_sms_telegram_link_service)
]
SmsIngestServiceDep = Annotated[SmsIngestService, Depends(get_sms_ingest_service)]
