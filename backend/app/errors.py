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


def _error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


# Типы ошибок pydantic v2 для поля IPvAnyAddress: значение присутствует, но
# семантически невалидно. По 04-api.md это 422 unprocessable, а не 400.
_IP_VALUE_ERROR_TYPES = frozenset({"ip_any_address", "ip_v4_address", "ip_v6_address"})


def _has_invalid_ip_error(errors: Sequence[Any]) -> bool:
    """True, если среди ошибок валидации есть семантически невалидный IP в поле `ip`."""
    for err in errors:
        loc = err.get("loc", ())
        if loc and loc[-1] == "ip" and err.get("type") in _IP_VALUE_ERROR_TYPES:
            return True
    return False


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
        # Семантически некорректный IP → 422 unprocessable (04-api.md): значение
        # поля `ip` присутствует, но не является валидным IP. Структурные ошибки
        # (отсутствует поле / неверная форма тела) остаются 400 validation_error.
        if _has_invalid_ip_error(raw_errors):
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                content=_error_body("unprocessable", "Невалидный IP-адрес", details),
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
