"""Структурированное логирование (structlog) с маскированием секретов.

Гарантирует, что пароли, токены и ключи не попадают в логи (05-security.md).
"""

from __future__ import annotations

import logging

import structlog
from structlog.typing import EventDict, WrappedLogger

# Ключи, значения которых маскируются в любом лог-событии.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "smtp_password",
        "ssh_password",
        "ssh_password_encrypted",
        "ansible_password",
        "authorization",
        "token",
        "access_token",
        "jwt",
        "jwt_secret",
        "fernet_key",
        "secret",
        "admin_password",
    }
)

_MASK = "***"


def _mask_secrets(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """Заменяет значения чувствительных ключей на маску."""
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = _MASK
    return event_dict


def configure_logging(*, json_logs: bool) -> None:
    """Настраивает structlog один раз при старте приложения."""
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _mask_secrets,
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Возвращает именованный структурированный логгер."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
