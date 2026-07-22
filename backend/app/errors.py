"""Единый формат ошибок API и обработчики исключений (04-api.md#единый-формат-ошибки)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Доменная ошибка с кодом и HTTP-статусом по контракту 04-api.md."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


# --- Сообщения `504 mail_timeout` по контексту пути (ADR-053 §4; словарь строк —
# 08-design-system.md#локализация-ui-русский-словарь-строк). Код контракта один
# (`mail_timeout`), различается только текст для пользователя. ---
MAIL_TIMEOUT_TEST_MESSAGE = (
    "Проверка не завершилась за отведённое время: почтовый сервер не ответил. "
    "Проверьте хост и порт."
)
MAIL_TIMEOUT_MAILBOX_MESSAGE = (
    "Операция не завершилась вовремя. Состояние ящика могло измениться — обновите список."
)
MAIL_TIMEOUT_REPLY_MESSAGE = (
    "Отправка не подтверждена: сервис не ответил вовремя. "
    "Письмо могло быть отправлено — проверьте перед повтором."
)
# Дефолт фабрики: формулировка операции над ящиком (delete/sync/authorize — отдельной
# строки словарь не задаёт).
MAIL_TIMEOUT_DEFAULT_MESSAGE = MAIL_TIMEOUT_MAILBOX_MESSAGE


# --- Фабрики типовых ошибок (коды строго из 04-api.md) ---


def invalid_credentials() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_credentials",
        message="Неверный логин или пароль",
    )


def unauthorized() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="unauthorized",
        message="Требуется авторизация",
    )


def forbidden() -> AppError:
    return AppError(
        status_code=status.HTTP_403_FORBIDDEN,
        code="forbidden",
        message="Недостаточно прав",
    )


def user_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="user_not_found",
        message="Пользователь не найден",
    )


def role_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="role_not_found",
        message="Роль не найдена",
    )


def username_taken() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="username_taken",
        message="Пользователь с таким именем уже существует",
    )


def role_name_taken() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="role_name_taken",
        message="Роль с таким именем уже существует",
    )


def user_in_use() -> AppError:
    """Пользователь владеет документами/вложениями — hard-delete запрещён. 409 (TD-077).

    Зеркало FK `document_nodes.owner_id` / `document_attachments.created_by`
    `ON DELETE RESTRICT` (ADR-059/ADR-068) — тот же принцип, что `role_in_use` для
    `users.role_id`: нарушение целостности обязано выходить прикладным `409`, а не
    `500 internal_error`. Состав узлов НЕ раскрывается (анти-энумерация ADR-059).
    """
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="user_in_use",
        message="Пользователь владеет документами или вложениями — удаление запрещено",
    )


def role_in_use() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="role_in_use",
        message="Роль назначена пользователям — удаление запрещено",
    )


def email_taken() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="email_taken",
        message="Пользователь с таким email уже существует",
    )


def telegram_taken() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="telegram_taken",
        message="Пользователь с таким телеграмом уже существует",
    )


def password_already_set() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="password_already_set",
        message="Пароль уже установлен",
    )


def user_is_team_leader() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="user_is_team_leader",
        message="Пользователь — лидер команды, удаление запрещено",
    )


def team_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="team_not_found",
        message="Команда не найдена",
    )


def team_name_taken() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="team_name_taken",
        message="Команда с таким названием уже существует",
    )


def server_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="server_not_found",
        message="Сервер не найден",
    )


def server_conflict() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="server_conflict",
        message="Сервер с таким IP уже существует",
    )


def ai_key_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="ai_key_not_found",
        message="AI-ключ не найден",
    )


def proxy_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="proxy_not_found",
        message="Прокси не найден",
    )


def secret_not_set() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="secret_not_set",
        message="Секрет не задан",
    )


def backend_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="backend_not_found",
        message="Бэк не найден",
    )


def backend_code_taken() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="backend_code_taken",
        message="Бэк с таким кодом уже существует",
    )


def sms_number_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="sms_number_not_found",
        message="Номер не найден",
    )


def sms_team_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="sms_team_not_found",
        message="Команда не найдена",
    )


def sms_operator_not_provisioned() -> AppError:
    return AppError(
        status_code=status.HTTP_403_FORBIDDEN,
        code="sms_operator_not_provisioned",
        message="Ваш Telegram не сопоставлен с оператором CRM",
    )


def mail_operator_not_provisioned() -> AppError:
    """Telegram Mini App `/tg/mail`: username не сопоставлен с CRM-пользователем.

    ADR-044 §6/§7 (симметрично `sms_operator_not_provisioned`, ADR-031). 403.
    """
    return AppError(
        status_code=status.HTTP_403_FORBIDDEN,
        code="mail_operator_not_provisioned",
        message="Ваш Telegram не привязан к пользователю CRM. Обратитесь к администратору.",
    )


def invalid_twilio_signature() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_twilio_signature",
        message="Неверная подпись Twilio",
    )


def invalid_init_data() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="invalid_init_data",
        message="Невалидные данные Telegram",
    )


def init_data_expired() -> AppError:
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="init_data_expired",
        message="Данные Telegram устарели",
    )


def invalid_webhook_secret() -> AppError:
    return AppError(
        status_code=status.HTTP_403_FORBIDDEN,
        code="invalid_webhook_secret",
        message="Неверный секрет webhook",
    )


def invalid_cursor() -> AppError:
    return AppError(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="invalid_cursor",
        message="Битый курсор пагинации",
    )


def invalid_limit() -> AppError:
    return AppError(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="invalid_limit",
        message="Недопустимый размер страницы",
    )


def twilio_error() -> AppError:
    return AppError(
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="twilio_error",
        message="Сбой Twilio API",
    )


def twilio_not_configured() -> AppError:
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="twilio_not_configured",
        message="Twilio не настроен",
    )


def unprocessable(message: str, details: Any = None) -> AppError:
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="unprocessable",
        message=message,
        details=details,
    )


def rate_limited() -> AppError:
    return AppError(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        code="rate_limited",
        message="Слишком много попыток входа, попробуйте позже",
    )


def prometheus_unavailable() -> AppError:
    return AppError(
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="prometheus_unavailable",
        message="Prometheus недоступен",
    )


def provisioning_unavailable() -> AppError:
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="provisioning_unavailable",
        message="Невозможно запустить провижининг",
    )


def validation_error(message: str = "Невалидные данные запроса", details: Any = None) -> AppError:
    """Структурная/диапазонная ошибка параметров запроса (400 validation_error).

    Для ручной валидации в сервисах (напр. `limit` вне 1..200 в модуле почты), когда
    нужен контроль прецеденции над другими проверками. Автоматические ошибки формы
    тела/пути формирует обработчик RequestValidationError (тот же код).
    """
    return AppError(
        status_code=status.HTTP_400_BAD_REQUEST,
        code="validation_error",
        message=message,
        details=details,
    )


def mail_unavailable() -> AppError:
    return AppError(
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="mail_unavailable",
        message="Почтовый сервис временно недоступен",
    )


def mail_timeout(message: str = MAIL_TIMEOUT_DEFAULT_MESSAGE) -> AppError:
    """Операция почты не завершилась вовремя (504 `mail_timeout`, ADR-053 §3).

    Два источника (различаются по `MailTimeout.status_code`, ADR-053 §2.1): `504` ОТ
    агрегатора (его прокси не дождался) — на ЛЮБОЙ категории путей; собственный таймаут
    CRM (read-бюджет или overall-deadline) — только на mail-server-путях (на быстрых он
    даёт `502 mail_unavailable`). Это НЕ «сервис недоступен»: агрегатор доступен, но не
    успел. Автоматически НЕ ретраится — состояние операции неопределённо.

    `message` — по контексту пути (словарь 08-design-system.md, ADR-053 §4); дефолт —
    формулировка операции над ящиком.
    """
    return AppError(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        code="mail_timeout",
        message=message,
    )


def mail_send_failed() -> AppError:
    """Удалённый SMTP отклонил отправку reply (502 `mail_send_failed`, ADR-053 §2).

    Проброс `502 smtp_failed` агрегатора: сам агрегатор РАБОТАЛ — это не «сервис
    недоступен».
    """
    return AppError(
        status_code=status.HTTP_502_BAD_GATEWAY,
        code="mail_send_failed",
        message="Почтовый сервер не принял письмо. Проверьте настройки SMTP ящика.",
    )


def mail_imap_failed() -> AppError:
    """IMAP-проверка ящика не прошла (422 `mail_imap_failed`, ADR-053 §2).

    Проброс `422 imap_login_failed` агрегатора (`test`/create/`PATCH` кредов).
    """
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="mail_imap_failed",
        message="Не удалось подключиться к IMAP. Проверьте хост, порт, SSL и пароль.",
    )


def mail_smtp_failed() -> AppError:
    """SMTP-проверка ящика не прошла (422 `mail_smtp_failed`, ADR-053 §2).

    Проброс `422 smtp_login_failed` агрегатора (`test`/create/`PATCH` кредов).
    """
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="mail_smtp_failed",
        message="Не удалось подключиться к SMTP. Проверьте хост, порт, SSL/STARTTLS и пароль.",
    )


def mail_invalid_host() -> AppError:
    """SSRF-guard агрегатора отклонил хост IMAP/SMTP (422 `mail_invalid_host`, ADR-053 §2).

    Проброс `422 invalid_host` агрегатора: приватный/локальный хост.
    """
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="mail_invalid_host",
        message="Недопустимый адрес сервера: приватные и локальные хосты запрещены.",
    )


def mail_message_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="mail_message_not_found",
        message="Письмо не найдено",
    )


def mail_not_configured() -> AppError:
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="mail_not_configured",
        message="Сервис почт не настроен",
    )


def mail_ingest_not_configured() -> AppError:
    """Push-приёмник выключен: пуст `MAIL_PUSH_SECRET` (ADR-044 §3). 503."""
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="mail_ingest_not_configured",
        message="Приём почты от агрегатора не настроен",
    )


def not_authenticated() -> AppError:
    """Невалидная HMAC-подпись / протухший timestamp push-контракта (ADR-044 §3). 401."""
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code="not_authenticated",
        message="Не аутентифицирован",
    )


def oauth_state_expired() -> AppError:
    """`crm_state.exp` в прошлом на `/api/mail/oauth/ingest` (ADR-045 §3). 410.

    Консент завершён после TTL: ящик создан в агрегаторе, но CRM не привяжет его к
    команде по этому уведомлению → доедет через reconcile-pull (TD-047).
    """
    return AppError(
        status_code=status.HTTP_410_GONE,
        code="oauth_state_expired",
        message="Ссылка подключения Outlook устарела",
    )


def mail_mailbox_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="mail_mailbox_not_found",
        message="Почтовый ящик не найден",
    )


def mail_tag_not_found() -> AppError:
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="mail_tag_not_found",
        message="Тег не найден",
    )


def mail_conflict() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="mail_conflict",
        message="Конфликт: ящик или тег с такими данными уже существует",
    )


def team_mail_group_taken() -> AppError:
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="team_mail_group_taken",
        message="Эта группа почты уже привязана к другой команде",
    )


def document_node_not_found() -> AppError:
    """Узла нет ИЛИ он невидим по роли (анти-энумерация, ADR-059). 404.

    Чтение/правка/удаление вне видимости неотличимы от несуществующего узла — НЕ 403
    (05-security.md#видимость-документов-по-ролям).
    """
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="document_node_not_found",
        message="Документ не найден",
    )


def document_node_conflict() -> AppError:
    """`PATCH /api/documents/nodes/{id}` с `expected_version` ≠ текущему (TD-064). 409."""
    return AppError(
        status_code=status.HTTP_409_CONFLICT,
        code="document_node_conflict",
        message="Документ изменён другим пользователем. Обновите и повторите.",
    )


def document_upload_invalid() -> AppError:
    """`POST /api/documents/upload`: файл не `.md` / превышен лимит / битый UTF-8. 422."""
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="document_upload_invalid",
        message="Недопустимый файл: только .md в кодировке UTF-8 в пределах лимита размера.",
    )


def document_copy_cycle() -> AppError:
    """`POST /api/documents/nodes/{id}/copy`: цель копирования — сам узел или потомок. 422."""
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="document_copy_cycle",
        message="Нельзя скопировать узел внутрь самого себя или своего потомка.",
    )


def document_attachment_not_found() -> AppError:
    """Вложения нет ЛИБО узел-владелец невидим по роли ЛИБО узел soft-deleted (ADR-068). 404.

    **Единый код на все три случая** (анти-энумерация): различие кодов сообщало бы о
    существовании невидимого узла — та же логика, что у `document_node_not_found`.
    """
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code="document_attachment_not_found",
        message="Изображение не найдено",
    )


def document_attachment_invalid() -> AppError:
    """`POST /api/documents/nodes/{id}/attachments`: тип вне whitelist / расхождение
    заявленного и фактического типа / превышен `DOCUMENTS_MAX_IMAGE_BYTES` (ADR-068 §2). 422.

    Единый код: детализация «слишком большой» vs «не тот тип» ничего не даёт атакующему
    и не нужна UI сверх текста сообщения.
    """
    return AppError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="document_attachment_invalid",
        message=(
            "Недопустимое изображение: только PNG, JPEG, WebP или GIF " "в пределах лимита размера."
        ),
    )


def documents_external_not_configured() -> AppError:
    """Внешний read-only контур документов выключен: пуст `DOCUMENTS_API_KEY` (ADR-060 §1). 503."""
    return AppError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="documents_external_not_configured",
        message="Внешний доступ к документам не настроен",
    )


def document_node_gone(*, node_id: Any, deleted_at: Any, content_version: int) -> AppError:
    """Внешний `GET /api/external/documents/{id}` для удалённого узла (tombstone, ADR-060 §3). 410.

    Тело несёт tombstone-детали `{id, deleted_at, content_version}` (04-api.md#external-documents):
    RAG по ним удаляет узел из индекса. `content_md` не отдаётся.
    """
    return AppError(
        status_code=status.HTTP_410_GONE,
        code="document_node_gone",
        message="Документ удалён",
        details={
            "id": str(node_id),
            "deleted_at": deleted_at.isoformat(),
            "content_version": content_version,
        },
    )


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


# Типы ошибок pydantic v2 для поля IPvAnyAddress: значение присутствует, но
# семантически невалидно. По 04-api.md это 422 unprocessable, а не 400.
_IP_VALUE_ERROR_TYPES = frozenset({"ip_any_address", "ip_v4_address", "ip_v6_address"})

# Типы ошибок pydantic v2 для `port` вне диапазона 1..65535: значение присутствует,
# но семантически недопустимо. По 04-api.md#proxies это 422 unprocessable, а не 400.
_PORT_RANGE_ERROR_TYPES = frozenset({"greater_than_equal", "less_than_equal"})


def _has_invalid_ip_error(errors: Sequence[Any]) -> bool:
    """True, если среди ошибок валидации есть семантически невалидный IP в поле `ip`."""
    for err in errors:
        loc = err.get("loc", ())
        if loc and loc[-1] == "ip" and err.get("type") in _IP_VALUE_ERROR_TYPES:
            return True
    return False


def _semantic_error_message(errors: Sequence[Any]) -> str | None:
    """Возвращает сообщение 422 unprocessable для семантически некорректных полей.

    По 04-api.md это `422 unprocessable` (значение присутствует, но недопустимо),
    а не `400 validation_error`:
      - невалидный IP в поле `ip`;
      - `provider` вне enum (тип `enum`) в поле `provider` (modules/ai-keys);
      - `proxy_type` вне enum в поле `proxy_type` (modules/proxies);
      - `port` вне диапазона 1..65535 в поле `port` (modules/proxies).
    Структурные ошибки (отсутствует поле / неверная форма тела) → 400.
    """
    if _has_invalid_ip_error(errors):
        return "Невалидный IP-адрес"
    for err in errors:
        loc = err.get("loc", ())
        if not loc:
            continue
        field = loc[-1]
        err_type = err.get("type")
        if field == "provider" and err_type == "enum":
            return "Недопустимый провайдер"
        if field == "proxy_type" and err_type == "enum":
            return "Недопустимый тип прокси"
        if field == "port" and err_type in _PORT_RANGE_ERROR_TYPES:
            return "Недопустимый порт"
    return None


def register_exception_handlers(app: FastAPI) -> None:
    """Регистрирует обработчики, приводящие все ошибки к единому формату."""

    @app.exception_handler(AppError)
    async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        raw_errors = exc.errors()
        details = [
            {"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]}
            for err in raw_errors
        ]
        # Семантически некорректное значение → 422 unprocessable (04-api.md):
        # невалидный IP в `ip` или `provider` вне enum. Структурные ошибки
        # (отсутствует поле / неверная форма тела) остаются 400 validation_error.
        semantic_message = _semantic_error_message(raw_errors)
        if semantic_message is not None:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=_error_body("unprocessable", semantic_message, details),
            )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_error_body("validation_error", "Невалидные данные запроса", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = (
            "unauthorized" if exc.status_code == status.HTTP_401_UNAUTHORIZED else "internal_error"
        )
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            code = "not_found"
        message = exc.detail if isinstance(exc.detail, str) else "Ошибка запроса"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code, message),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("internal_error", "Внутренняя ошибка сервера"),
        )
