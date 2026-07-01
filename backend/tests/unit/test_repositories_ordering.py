"""Unit-тест сортировки `list_all` репозиториев (04-api.md, 03-data-model.md).

Без БД: перехватываем скомпилированный `Select` и проверяем, что оба репозитория
сортируют `ORDER BY position ASC, created_at DESC, id ASC` (при равных position —
новее выше; id — финальный тай-брейк). Это нормативный порядок для `GET /api/servers`
и `GET /api/ai-keys` (03-data-model.md#колонка-position-порядок-карточек).
"""

from __future__ import annotations

from typing import Any, cast

from app.repositories.ai_key_repository import AiKeyRepository
from app.repositories.server_repository import ServerRepository


class _FakeResult:
    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return []


class _CapturingSession:
    """Перехватывает `Select`, переданный в execute (никаких запросов к БД)."""

    def __init__(self) -> None:
        self.stmt: Any = None

    async def execute(self, stmt: Any) -> _FakeResult:
        self.stmt = stmt
        return _FakeResult()


async def test_server_repo_list_all_orders_position_created_at_id() -> None:
    session = _CapturingSession()
    repo = ServerRepository(cast(Any, session))

    await repo.list_all()

    sql = str(session.stmt)
    assert "ORDER BY" in sql
    assert "servers.position ASC" in sql
    assert "servers.created_at DESC" in sql
    assert "servers.id ASC" in sql
    # position ДО created_at ДО id.
    assert sql.index("servers.position ASC") < sql.index("servers.created_at DESC")
    assert sql.index("servers.created_at DESC") < sql.index("servers.id ASC")


async def test_ai_key_repo_list_all_orders_position_created_at_id() -> None:
    session = _CapturingSession()
    repo = AiKeyRepository(cast(Any, session))

    await repo.list_all()

    sql = str(session.stmt)
    assert "ORDER BY" in sql
    assert "ai_keys.position ASC" in sql
    assert "ai_keys.created_at DESC" in sql
    assert "ai_keys.id ASC" in sql
    assert sql.index("ai_keys.position ASC") < sql.index("ai_keys.created_at DESC")
    assert sql.index("ai_keys.created_at DESC") < sql.index("ai_keys.id ASC")
