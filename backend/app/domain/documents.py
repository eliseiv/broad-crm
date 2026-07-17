"""Чистые доменные типы модуля «Документы» (modules/documents, ADR-059).

`DocumentScope` — ролевая видимость узлов дерева. Хранится здесь (а не в `api/deps.py`),
чтобы разорвать цикл импорта deps ↔ сервис (образец `SmsScope`). Вычисляется фабрикой
`get_document_scope` в `api/deps.py`.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DocumentScope:
    """Ролевая видимость документов (05-security.md#видимость-документов-по-ролям, ADR-059).

    Два уровня доступа независимы: `documents:view` (гейт страницы/API — `require(...)`) и
    per-node фильтр видимости по роли (этот scope).

    - `sees_all` — «видит все узлы» ⇔ `is_superadmin` ИЛИ роль владеет полным каталогом
      прав (тот же admin-предикат, что «видит все SMS/почты», ADR-032). При True per-role
      фильтр не применяется — актор видит и правит любой узел.
    - `role_id` — единственная роль пользователя (`users.role_id`, ADR-021). Узел виден ⇔
      он публичен внутри модуля (нет `restricted`-предка до корня) ИЛИ его эффективный
      набор ролей (`document_node_roles` ближайшего `restricted`-предка) содержит `role_id`.
      У admin-уровня `role_id` не используется (`sees_all=True` перекрывает); у него он
      может быть `None` (консольный супер-админ без строки в `users`).
    """

    sees_all: bool
    role_id: uuid.UUID | None


# --- Компаундный keyset-курсор внешнего синка (ADR-060 §3, 04-api.md#external-documents) ---
# Пара `(updated_at, id)` (техника — образец mail-курсора, но направление ASC и поле
# `updated_at`, а `id` — UUID, а не int). `updated_at` не уникален (массовая мутация одной
# транзакцией) ⇒ компаундность обязательна: пагинация по одной дате даёт пропуски/дубли
# на границах страниц.

_CURSOR_SEP = "|"


class DocumentCursorError(Exception):
    """Битый/недекодируемый keyset-курсор внешнего синка (сервис маппит в 400 validation_error)."""


def encode_document_cursor(updated_at: datetime, node_id: uuid.UUID) -> str:
    """Кодирует позицию `(updated_at, id)` в opaque base64url-токен (без padding).

    `updated_at` — tz-aware (`TIMESTAMPTZ`); `isoformat`/`fromisoformat` round-трипят без
    потери. `node_id` — UUID узла.
    """
    raw = f"{updated_at.isoformat()}{_CURSOR_SEP}{node_id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_document_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Декодирует opaque-курсор в `(updated_at, id)`.

    :raises DocumentCursorError: токен пуст, недекодируем или структурно неверен (в т.ч.
        наивный datetime или невалидный UUID — позиция всегда кодируется из TIMESTAMPTZ+UUID).
    """
    if not cursor:
        raise DocumentCursorError("Пустой курсор")
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        iso, id_str = raw.rsplit(_CURSOR_SEP, 1)
        updated_at = datetime.fromisoformat(iso)
        node_id = uuid.UUID(id_str)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DocumentCursorError("Битый курсор пагинации") from exc
    if updated_at.tzinfo is None:
        raise DocumentCursorError("Курсор без таймзоны")
    return updated_at, node_id
