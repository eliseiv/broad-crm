"""Модель таблицы `servers` (03-data-model.md)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProvisionStatus(str, enum.Enum):
    """Конечный автомат статуса провижининга (03-data-model.md)."""

    pending = "pending"
    installing = "installing"
    online = "online"
    error = "error"


class Server(Base):
    """Реестр серверов. Метрики НЕ хранятся (источник — Prometheus, ADR-003)."""

    __tablename__ = "servers"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 64", name="ck_servers_name_len"),
        CheckConstraint("char_length(ssh_user) BETWEEN 1 AND 64", name="ck_servers_ssh_user_len"),
        CheckConstraint("exporter_port BETWEEN 1 AND 65535", name="ck_servers_exporter_port"),
        CheckConstraint(
            "provision_status IN ('pending','installing','online','error')",
            name="ck_servers_provision_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str] = mapped_column(INET, nullable=False, unique=True)
    ssh_user: Mapped[str] = mapped_column(Text, nullable=False)
    ssh_password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    exporter_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("9100"))
    provision_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    @property
    def instance(self) -> str:
        """Prometheus label `instance` = `<ip>:<exporter_port>`."""
        return f"{self.ip}:{self.exporter_port}"
