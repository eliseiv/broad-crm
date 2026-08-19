"""Модели Postgres-снимка страницы «Юзеры бэков» (03-data-model.md, ADR-080 §2).

Снимок — **read-only-зеркало** CRM Admin API бэков, а не источник истины: пишет только
фоновый воркер `BackendUsersSnapshotService` (+ best-effort touch после admin-мутаций,
ADR-080 §4), все точечные и пишущие пути страницы остаются live.

- `BackendUserSnapshotSource` — одна строка на бэк с admin-ключом: метка свежести
  (`refreshed_at`), сбой последнего цикла (`error_message`/`failed_at`), снимок
  `GET {P}/stats` и агрегат `api_costs` с двумя признаками полноты
  (`revenue_backfill_done`, `revenue_supported`).
- `BackendUserSnapshot` — зеркало элемента `GET {P}/users` + экономика карточки
  (`api_cost_usd`, `api_cost_providers` — **сырые** ключи бэка; нормализация провайдеров
  выполняется при агрегации, чтобы смена правила не требовала повторного обхода).

Секретов в снимке нет: admin-ключ живёт только в `backends.admin_api_key_encrypted`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BackendUserSnapshotSource(Base):
    """Состояние снимка одного бэка-источника (ADR-080 §2)."""

    __tablename__ = "backend_user_snapshot_sources"

    backend_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backends.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Конец последнего УСПЕШНОГО цикла. NULL — снимок ещё ни разу не собран
    # (UI: «Снимок формируется…»); источник `snapshot_at` ответа (MIN по источникам).
    refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Непусто ⇔ источник попадает в `errors[]` ответа. Успешный цикл обнуляет поле.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stats_users_total: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    stats_paid_users: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    stats_payments_sum_usd: Mapped[float] = mapped_column(
        Float, nullable=False, server_default=text("0")
    )
    # {"openai":…,"anthropic":…,"fal":…,"other":…} — lifetime-агрегат по нормализованным
    # провайдерам; пересчитывается воркером из `api_cost_providers` строк снимка.
    api_costs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # false ⇒ карточки ещё добираются квотой (очередь — `revenue_refreshed_at IS NULL`).
    revenue_backfill_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Отдаёт ли бэк блок `revenue` (ADR-080 §5): выставляется по ПЕРВОЙ успешно добранной
    # карточке КАЖДОГО цикла (бэк, внедривший v1.1, переключается в `true` сам). NULL —
    # карточек ещё не добирали. Второй дизъюнкт `api_costs.partial` — строго `IS FALSE`.
    revenue_supported: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class BackendUserSnapshot(Base):
    """Строка снимка пользователя бэка (зеркало `BackendUserItem` + экономика)."""

    __tablename__ = "backend_user_snapshots"
    __table_args__ = (
        Index("ix_backend_user_snapshots_registered_at", text("registered_at DESC")),
        Index(
            "ix_backend_user_snapshots_backend_registered_at",
            "backend_id",
            text("registered_at DESC"),
        ),
        Index("ix_backend_user_snapshots_user_id", "user_id"),
        Index("ix_backend_user_snapshots_external_id", "external_id"),
    )

    backend_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("backends.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Идентификатор пользователя У БЭКА (`id` элемента контракта). `text`, а не `uuid`:
    # контракт UUID-формы не требует.
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_paid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    payments_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    renewals_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens: Mapped[float | None] = mapped_column(Float, nullable=True)
    subscription_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subscription_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    plan_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ключ сортировки списка и фильтра периода.
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # NULL — «не измерено» (бэк уровня v1 без блока `revenue` либо карточка не добрана),
    # НЕ ноль.
    api_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # `none_as_null=True` обязателен: по умолчанию SQLAlchemy пишет питоновский `None` в
    # jsonb-колонку как ЗНАЧЕНИЕ `'null'`, а не SQL NULL — и `jsonb_each_text` на такой
    # строке падает («cannot call jsonb_each_text on a non-object»), роняя пересчёт
    # `api_costs` для всего бэка из-за одного пользователя без блока `revenue`.
    api_cost_providers: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    # NULL ⇒ строка в очереди backfill-квоты. Метка ставится и тогда, когда бэк вернул
    # карточку БЕЗ блока `revenue`: перечитывать такую строку бессмысленно.
    revenue_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
