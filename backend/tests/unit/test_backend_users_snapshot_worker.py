"""Регресс-гейты воркера снимка «Юзеров бэков» (ADR-080 §2/§5).

Файл покрывает ИМЕННО те дефекты, которые были найдены ревью, — по одному гейту на
дефект. Полное сценарное покрытие (полный обход, delete-missing только при успешном
обходе, `revenue_supported` по первой карточке, `partial`) — зона qa.

Прогон без Postgres: воркер работает поверх фейкового `sessionmaker`, отдающего
in-memory репозиторий с тем же интерфейсом, и фейкового admin-клиента.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from app.errors import AppError, backend_admin_response_unusable, backend_admin_unavailable
from app.repositories.backend_user_snapshot_repository import build_fingerprint
from app.services.backend_users_snapshot_service import (
    BackendUsersSnapshotService,
    normalize_provider,
)

BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


# --- MINOR 2: нормализация провайдера — по ЗНАЧЕНИЮ, не по префиксу ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("openai", "openai"),
        ("OpenAI", "openai"),
        ("gpt-4o", "openai"),
        ("GPT-5", "openai"),
        ("anthropic", "anthropic"),
        ("claude-3-opus", "anthropic"),
        ("fal", "fal"),
        ("fal.ai", "fal"),
        ("fal_ai", "fal"),
        # Гейт дефекта: префиксное сравнение утащило бы эти имена в чужую строку сводки.
        ("falcon", "other"),
        ("openai-proxy-vendor", "other"),
        ("anthropic-reseller", "other"),
        ("deepseek", "other"),
    ],
)
def test_normalize_provider_matches_by_value_not_prefix(raw: str, expected: str) -> None:
    assert normalize_provider(raw) == expected


# --- MAJOR 3: fingerprint устойчив к tz (naive из API == aware из БД) --------


def test_fingerprint_treats_naive_and_aware_utc_as_equal() -> None:
    """Гейт дефекта: без нормализации changed-only-writes вырождался в полную перезапись.

    Из БД `TIMESTAMPTZ` приходит aware, из ответа бэка тот же момент может прийти naive —
    и кортежи не совпадали бы НИКОГДА (dirty-set = все пользователи каждые 15 минут).
    """
    naive = datetime(2026, 8, 13, 17, 1, 14)
    aware = datetime(2026, 8, 13, 17, 1, 14, tzinfo=UTC)

    from_api = build_fingerprint({"registered_at": naive, "subscription_expires_at": naive})
    from_db = build_fingerprint({"registered_at": aware, "subscription_expires_at": aware})

    assert from_api == from_db


def test_fingerprint_still_distinguishes_different_moments() -> None:
    """Нормализация не должна схлопывать РАЗНЫЕ моменты в один."""
    a = build_fingerprint({"registered_at": datetime(2026, 8, 13, 17, 1, 14)})
    b = build_fingerprint({"registered_at": datetime(2026, 8, 13, 17, 1, 15)})
    assert a != b


# --- Фейки инфраструктуры ---------------------------------------------------


class _FakeSnapshotRepo:
    """In-memory репозиторий снимка: интерфейс, которым пользуется воркер."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def fingerprints(self, _backend_id: uuid.UUID) -> dict[str, Any]:
        return dict(self._state["fingerprints"])

    async def upsert_rows(self, rows: list[dict[str, Any]]) -> None:
        self._state["upserted"].extend(rows)

    async def delete_rows(self, _backend_id: uuid.UUID, user_ids: Any) -> int:
        ids = list(user_ids)
        self._state["deleted"].extend(ids)
        return len(ids)

    async def backfill_candidates(self, _backend_id: uuid.UUID, limit: int) -> list[str]:
        return list(self._state["backfill_queue"])[:limit]

    async def count_pending_revenue(self, _backend_id: uuid.UUID) -> int:
        return int(self._state["pending"])

    async def set_revenue(self, **kwargs: Any) -> None:
        self._state["revenue_written"].append(kwargs["user_id"])

    async def sum_providers(self, _backend_id: uuid.UUID) -> dict[str, float]:
        return dict(self._state["provider_sums"])

    async def upsert_source(self, _backend_id: uuid.UUID, values: dict[str, Any]) -> None:
        self._state["source"].append(values)


def _service(state: dict[str, Any], *, revenue_batch: int = 2000) -> BackendUsersSnapshotService:
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={"backend_users_snapshot_revenue_batch": revenue_batch}
    )
    service = BackendUsersSnapshotService(
        sessionmaker=lambda: None,  # type: ignore[arg-type]
        settings=settings,
    )

    @asynccontextmanager
    async def _in_session() -> Any:
        yield _FakeSnapshotRepo(state)

    service._in_session = _in_session  # type: ignore[method-assign]
    return service


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "fingerprints": {},
        "upserted": [],
        "deleted": [],
        "backfill_queue": [],
        "pending": 0,
        "revenue_written": [],
        "provider_sums": {},
        "source": [],
    }
    base.update(overrides)
    return base


class _FakeClient:
    """Фейк admin-клиента: страница списка, stats и программируемые карточки."""

    def __init__(
        self,
        *,
        users: list[dict[str, Any]],
        detail: Any = None,
        detail_exc: Exception | None = None,
    ) -> None:
        self._users = users
        self._detail = detail
        self._detail_exc = detail_exc
        self.detail_calls: list[str] = []

    async def list_users(self, *, limit: int, offset: int) -> dict[str, Any]:
        page = self._users[offset : offset + limit]
        return {"total": len(self._users), "items": page}

    async def get_stats(self, **_kwargs: Any) -> dict[str, Any]:
        return {"users_total": len(self._users), "paid_users": 0, "payments_sum_usd": 0}

    async def get_user(self, user_id: str) -> dict[str, Any]:
        self.detail_calls.append(user_id)
        if self._detail_exc is not None:
            raise self._detail_exc
        return self._detail or {"id": user_id, "registered_at": "2026-08-13T17:01:14Z"}


def _user_row(index: int) -> dict[str, Any]:
    return {"id": f"u{index:05d}", "registered_at": "2026-08-13T17:01:14Z"}


# --- MAJOR 2: квота — потолок на ВЕСЬ цикл, а не только на хвост -------------


async def test_revenue_quota_caps_total_calls_including_dirty_set() -> None:
    """Гейт дефекта: холодный старт делал N вызовов вместо `revenue_batch`.

    На холодном старте снимок пуст ⇒ dirty-set = ВСЕ пользователи. Прежняя редакция
    резала квотой только добор из очереди, а весь dirty-set опрашивала целиком.
    """
    users = [_user_row(i) for i in range(50)]
    state = _state(backfill_queue=[f"q{i}" for i in range(50)])
    client = _FakeClient(users=users)

    service = _service(state, revenue_batch=10)
    await service._refresh_backend_inner(BACKEND_ID, client)

    assert len(client.detail_calls) == 10
    # Усечённый хвост dirty-set не потерян: `revenue_refreshed_at` у него остался NULL,
    # значит он сам стоит в очереди backfill следующего цикла.
    assert len(state["revenue_written"]) == 10


async def test_revenue_quota_remainder_goes_to_backfill_queue() -> None:
    """Остаток квоты после dirty-set добирается из очереди холодного старта."""
    users = [_user_row(0), _user_row(1)]
    state = _state(backfill_queue=["q0", "q1", "q2", "q3"])
    client = _FakeClient(users=users)

    service = _service(state, revenue_batch=5)
    await service._refresh_backend_inner(BACKEND_ID, client)

    assert len(client.detail_calls) == 5
    assert client.detail_calls[:2] == ["u00000", "u00001"]
    assert client.detail_calls[2:] == ["q0", "q1", "q2"]


# --- MAJOR 1: транспортный сбой карточки роняет ЦИКЛ, а не молча теряется ----


async def test_transport_failure_on_detail_fails_the_cycle() -> None:
    """Гейт дефекта: голый `except` записывал цикл успешным с занижённой экономикой."""
    state = _state()
    client = _FakeClient(users=[_user_row(0)], detail_exc=backend_admin_unavailable())

    service = _service(state)
    with pytest.raises(AppError) as exc:
        await service._refresh_backend_inner(BACKEND_ID, client)

    assert exc.value.code == "backend_admin_unavailable"
    # Строка источника НЕ помечена успешной: `refreshed_at` не проставлен.
    assert all("refreshed_at" not in values for values in state["source"])


async def test_transport_failure_keeps_previous_snapshot_and_records_error() -> None:
    """Сбой цикла: снимок прошлого цикла цел, в источник пишутся error_message/failed_at."""
    state = _state()
    client = _FakeClient(users=[_user_row(0)], detail_exc=backend_admin_unavailable())

    service = _service(state)
    await service._refresh_backend(BACKEND_ID, "veltrio", client)

    assert state["deleted"] == []  # снимок не прорежен
    assert len(state["source"]) == 1
    failure = state["source"][0]
    assert failure["error_message"]
    assert failure["failed_at"] is not None
    assert "refreshed_at" not in failure  # прошлая метка свежести не затёрта


async def test_missing_user_card_is_skipped_and_cycle_succeeds() -> None:
    """404 на одной карточке — не отказ источника: пользователь удалён у бэка."""
    from app.errors import backend_user_not_found

    state = _state()
    client = _FakeClient(users=[_user_row(0)], detail_exc=backend_user_not_found())

    service = _service(state)
    await service._refresh_backend_inner(BACKEND_ID, client)

    assert state["revenue_written"] == []
    assert state["source"][-1]["refreshed_at"] is not None


async def test_unusable_response_on_detail_is_skipped_and_cycle_succeeds() -> None:
    """`2xx` с негодным телом (ADR-073 §8.3) — тоже проблема одной карточки."""
    state = _state()
    client = _FakeClient(
        users=[_user_row(0)], detail_exc=backend_admin_response_unusable("html вместо json")
    )

    service = _service(state)
    await service._refresh_backend_inner(BACKEND_ID, client)

    assert state["revenue_written"] == []
    assert state["source"][-1]["refreshed_at"] is not None


# --- CRITICAL 2: удаляются только исчезнувшие строки (без NOT IN на весь список) ---


async def test_only_vanished_rows_are_deleted() -> None:
    """Разность считается по памяти: в `delete_rows` уходят ТОЛЬКО исчезнувшие id."""
    users = [_user_row(0), _user_row(1)]
    state = _state(
        fingerprints={"u00000": (), "u00001": (), "u99999": ()},
    )
    client = _FakeClient(users=users)

    service = _service(state, revenue_batch=0)
    await service._refresh_backend_inner(BACKEND_ID, client)

    assert state["deleted"] == ["u99999"]
