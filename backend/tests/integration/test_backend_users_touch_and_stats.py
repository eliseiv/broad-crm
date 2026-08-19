"""Best-effort touch снимка и стоимость `stats` (ADR-080 §3/§4; 06-testing-strategy.md, 216/220).

Два нормативных блока, оба про «сколько это стоит и что происходит при сбое»:

1. **Best-effort touch (§4).** После успешной admin-мутации строка снимка обновляется
   значениями ИЗ ОТВЕТА БЭКА — иначе оператор, начисливший токены, видел бы старое
   значение до следующего цикла воркера. Но **провал touch'а НЕ превращает состоявшуюся
   операцию в ошибку** (тот же принцип «сначала факт, затем интерпретация», что в
   ADR-073 §8, кросс-реф TD-081): у бэка изменение уже применено, и `500` из-за
   необновившегося зеркала подтолкнул бы оператора повторить **НЕидемпотентное**
   начисление токенов. Ответ обязан остаться `200`, а аудит — записанным.
2. **Стоимость `stats` (§3).** Без периода сводка складывается из строк источников —
   upstream-вызовов **ноль**. С периодом — ровно **один** `GET {P}/stats` на бэк
   (периодные суммы из снимка невыводимы, но дорогой путь ограничен одним запросом).

Устройство: РЕАЛЬНЫЕ роутер, сервис, репозитории и Postgres; подменён только
upstream-ТРАНСПОРТ (`tests/backend_admin_helpers.py`). Сбой touch'а моделируется
**настоящей** ошибкой SQL внутри `touch_row` — только так проверяется `rollback()`
общей сессии запроса: без него любой следующий SQL (в т.ч. финальный `commit`)
упал бы `PendingRollbackError`, и провал best-effort touch'а всё-таки уронил бы ответ.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from app.api import deps
from app.infra.backend_admin_client import _clear_prefix_cache
from app.infra.crypto import encrypt_secret
from app.models.service_backend import Backend
from app.repositories.backend_user_snapshot_repository import BackendUserSnapshotRepository
from backend_admin_helpers import FakeAdminTransport, RecordingLogger, install_transport
from conftest import make_principal
from httpx import ASGITransport, AsyncClient
from mail_s34_helpers import mail_db
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

FIRST_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
SECOND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
ADMIN_KEY = "admin-key-of-the-touched-backend"
USER_ID = "c18ae65b-a6ab-4a1b-a13a-40b4c0d23708"
REGISTERED_AT = datetime(2026, 8, 13, 17, 1, 14, tzinfo=UTC)

BASE = f"/api/backend-users/{FIRST_ID}/users/{USER_ID}"


@pytest.fixture(autouse=True)
def _reset_prefix_cache() -> Iterator[None]:
    """Кэш префиксов клиента — process-global; без сброса тесты зависят от порядка."""
    _clear_prefix_cache()
    yield
    _clear_prefix_cache()


@asynccontextmanager
async def _db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Два бэка-источника с admin-ключом, строка снимка и строки состояния источников."""
    async with mail_db() as sm:
        async with sm() as session:
            await session.execute(
                sa_text(
                    "TRUNCATE backend_user_snapshots, backend_user_snapshot_sources, "
                    "backends RESTART IDENTITY CASCADE"
                )
            )
            for backend_id, code, name in (
                (FIRST_ID, "veltrio", "232"),
                (SECOND_ID, "selquro", "Selquro"),
            ):
                session.add(
                    Backend(
                        id=backend_id,
                        code=code,
                        name=name,
                        domain=f"https://{code}.shop/",
                        admin_api_key_encrypted=encrypt_secret(ADMIN_KEY),
                    )
                )
            await session.commit()

        async with sm() as session:
            repo = BackendUserSnapshotRepository(session)
            await repo.upsert_rows(
                [
                    {
                        "backend_id": FIRST_ID,
                        "user_id": USER_ID,
                        "external_id": "EXT-1",
                        "is_paid": False,
                        "payments_count": 0,
                        "renewals_count": 0,
                        "tokens": 100.0,
                        "subscription_active": False,
                        "subscription_expires_at": None,
                        "plan_id": None,
                        "registered_at": REGISTERED_AT,
                    }
                ]
            )
            # Сводка снимка: 7 + 5 пользователей, 3 + 1 платящих — суммы источников.
            await repo.upsert_source(
                FIRST_ID,
                {
                    "refreshed_at": datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
                    "stats_users_total": 7,
                    "stats_paid_users": 3,
                    "stats_payments_sum_usd": 12.5,
                },
            )
            await repo.upsert_source(
                SECOND_ID,
                {
                    "refreshed_at": datetime(2026, 8, 19, 12, 0, tzinfo=UTC),
                    "stats_users_total": 5,
                    "stats_paid_users": 1,
                    "stats_payments_sum_usd": 7.5,
                },
            )
            await session.commit()
        yield sm


def _app(sm: async_sessionmaker[AsyncSession]) -> Any:
    """Приложение с РЕАЛЬНЫМИ сервисом и репозиториями поверх тестовой сессии."""
    from app.config import get_settings
    from app.main import create_app

    app = create_app(get_settings())

    async def _session() -> AsyncIterator[AsyncSession]:
        async with sm() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_current_principal] = lambda: make_principal()
    return app


def _transport(monkeypatch: pytest.MonkeyPatch) -> FakeAdminTransport:
    """Исправный бэк: сводка и обе admin-мутации отвечают по контракту.

    Правило `GET /products` обязательно, даже если тест продукты не читает: на холодном
    кэше (фикстура `_reset_prefix_cache`) клиент определяет префикс контракта через
    probe `GET /products` (ADR-072 §4а). Без правила probe получает умолчание `404` на
    обоих кандидатах и ЛЮБАЯ операция падает `502 backend_admin_not_supported`.
    """
    transport = FakeAdminTransport()
    transport.on("GET", "/products", status=200, json_body={"items": []})
    transport.on(
        "GET",
        "/stats",
        status=200,
        json_body={"users_total": 4, "paid_users": 2, "payments_sum_usd": 9.0},
    )
    transport.on(
        "POST", f"/users/{USER_ID}/tokens", status=200, json_body={"id": USER_ID, "tokens": 150.0}
    )
    transport.on(
        "POST",
        f"/users/{USER_ID}/subscription",
        status=200,
        json_body={
            "id": USER_ID,
            "tokens": 300.0,
            "subscription_active": True,
            "subscription_expires_at": "2026-12-31T00:00:00Z",
            "applied": True,
        },
    )
    install_transport(monkeypatch, transport)
    return transport


async def _snapshot_row(sm: async_sessionmaker[AsyncSession]) -> Any:
    async with sm() as session:
        row = await session.execute(
            sa_text(
                "SELECT tokens, subscription_active, subscription_expires_at "
                "FROM backend_user_snapshots WHERE backend_id = :b AND user_id = :u"
            ),
            {"b": str(FIRST_ID), "u": USER_ID},
        )
        return row.one()


def _audit(recorder: RecordingLogger) -> list[dict[str, Any]]:
    """События аудита admin-операций (`backend_admin_action`)."""
    return recorder.named("backend_admin_action")


def _record_audit(monkeypatch: pytest.MonkeyPatch) -> RecordingLogger:
    import app.infra.audit as audit_mod

    recorder = RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", recorder)
    return recorder


def _break_touch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ломает `touch_row` НАСТОЯЩЕЙ ошибкой SQL (транзакция уходит в aborted).

    Не `raise RuntimeError`: смысл гейта в том, что после сбоя statement'а Postgres
    держит транзакцию запроса в состоянии «aborted», и без `rollback()` ЛЮБОЙ следующий
    SQL (включая финальный `commit` зависимости сессии) упал бы — то есть провал
    best-effort touch'а всё-таки уронил бы ответ операции.
    """

    async def _boom(self: BackendUserSnapshotRepository, **_kwargs: Any) -> None:
        await self.session.execute(sa_text("SELECT crm_no_such_function_for_test()"))

    monkeypatch.setattr(BackendUserSnapshotRepository, "touch_row", _boom)


# =============================================================================
# Best-effort touch (ADR-080 §4)
# =============================================================================


async def test_tokens_touch_updates_snapshot_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный `POST …/tokens` → строка снимка обновлена `tokens` ИЗ ОТВЕТА бэка."""
    _transport(monkeypatch)
    async with _db() as sm:
        async with AsyncClient(
            transport=ASGITransport(app=_app(sm)), base_url="http://test"
        ) as client:
            response = await client.post(f"{BASE}/tokens", json={"amount": 50})

        assert response.status_code == 200
        assert response.json()["tokens"] == 150.0
        tokens, _active, _expires = await _snapshot_row(sm)
        assert tokens == 150.0


async def test_tokens_touch_failure_keeps_operation_successful_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ Сбой touch'а НЕ меняет исход операции: ответ `200`, аудит записан.

    Негативный ассерт на `500` обязателен: начисление токенов НЕидемпотентно, и ошибка
    после состоявшегося начисления провоцирует повтор.
    """
    _transport(monkeypatch)
    recorder = _record_audit(monkeypatch)
    _break_touch(monkeypatch)

    async with _db() as sm:
        async with AsyncClient(
            transport=ASGITransport(app=_app(sm)), base_url="http://test"
        ) as client:
            response = await client.post(f"{BASE}/tokens", json={"amount": 50})

        assert response.status_code == 200
        assert response.json()["tokens"] == 150.0
        # Зеркало не обновилось — и это единственное последствие сбоя.
        tokens, _active, _expires = await _snapshot_row(sm)
        assert tokens == 100.0

    events = _audit(recorder)
    assert [e["action"] for e in events] == ["tokens_added"]
    assert events[0]["target_user_id"] == USER_ID


async def test_subscription_touch_updates_snapshot_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный `POST …/subscription` → в снимке обновлены токены, флаг и срок подписки."""
    _transport(monkeypatch)
    async with _db() as sm:
        async with AsyncClient(
            transport=ASGITransport(app=_app(sm)), base_url="http://test"
        ) as client:
            response = await client.post(
                f"{BASE}/subscription",
                json={"product_id": "p-1", "expires_in_days": 30, "grant_id": "g-1"},
            )

        assert response.status_code == 200
        tokens, active, expires = await _snapshot_row(sm)
        assert tokens == 300.0
        assert active is True
        assert expires == datetime(2026, 12, 31, tzinfo=UTC)


async def test_subscription_response_without_tokens_keeps_balance_in_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ Ответ БЕЗ поля `tokens` НЕ обнуляет баланс в снимке (ADR-072 §5: `null` ≠ `0`).

    Выдача плана баланс не меняет, поэтому бэк вправе поле не отдавать. Прежний дефолт
    `tokens: float = 0` превращал «поле отсутствует» в «на счету ноль», и best-effort
    touch затирал реальный баланс до следующего цикла воркера — оператор видел бы `0`
    у пользователя со `100` на счету. Подписочные поля при этом обязаны обновиться:
    они в ответе есть и они измерены.
    """
    transport = _transport(monkeypatch)
    # v1-бэк: подписка выдана, поля `tokens` в ответе нет вовсе.
    transport.on(
        "POST",
        f"/users/{USER_ID}/subscription",
        status=200,
        json_body={
            "id": USER_ID,
            "subscription_active": True,
            "subscription_expires_at": "2026-12-31T00:00:00Z",
            "applied": True,
        },
    )

    async with _db() as sm:
        async with AsyncClient(
            transport=ASGITransport(app=_app(sm)), base_url="http://test"
        ) as client:
            response = await client.post(
                f"{BASE}/subscription",
                json={"product_id": "p-1", "expires_in_days": 30, "grant_id": "g-1"},
            )

        assert response.status_code == 200
        # Наружу поле отдаётся как `null` — «не измерено», а не «ноль».
        assert response.json()["tokens"] is None
        tokens, active, expires = await _snapshot_row(sm)
        assert tokens == 100.0  # прежний баланс ЦЕЛ
        assert active is True  # измеренные поля подписки обновлены
        assert expires == datetime(2026, 12, 31, tzinfo=UTC)


async def test_subscription_touch_failure_keeps_operation_successful_and_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тот же инвариант для выдачи плана: `200` + аудит, несмотря на сбой зеркала."""
    _transport(monkeypatch)
    recorder = _record_audit(monkeypatch)
    _break_touch(monkeypatch)

    async with _db() as sm:
        async with AsyncClient(
            transport=ASGITransport(app=_app(sm)), base_url="http://test"
        ) as client:
            response = await client.post(
                f"{BASE}/subscription",
                json={"product_id": "p-1", "expires_in_days": 30, "grant_id": "g-1"},
            )

        assert response.status_code == 200
        assert response.json()["subscription_active"] is True
        tokens, active, _expires = await _snapshot_row(sm)
        assert tokens == 100.0
        assert active is False

    assert [e["action"] for e in _audit(recorder)] == ["subscription_granted"]


# =============================================================================
# Стоимость `stats` (ADR-080 §3)
# =============================================================================


async def test_stats_without_period_sums_sources_without_any_upstream_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без периода сводка — суммы строк источников, upstream-вызовов НОЛЬ.

    Ноль запросов — и есть смысл снимка: прежний live fan-out ходил в каждый бэк на
    КАЖДЫЙ рендер страницы.
    """
    transport = _transport(monkeypatch)
    async with (
        _db() as sm,
        AsyncClient(transport=ASGITransport(app=_app(sm)), base_url="http://test") as client,
    ):
        response = await client.get("/api/backend-users")

    assert response.status_code == 200
    stats = response.json()["stats"]
    assert stats["users_total"] == 12  # 7 + 5
    assert stats["paid_users"] == 4  # 3 + 1
    assert stats["payments_sum_usd"] == 20.0  # 12.5 + 7.5
    assert transport.requests == []


async def test_stats_with_period_issues_exactly_one_stats_request_per_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """С периодом — ровно ОДИН `GET {P}/stats` на бэк и ни одного запроса списка.

    Ассерт на ЧИСЛО вызовов, а не на «запрос был»: дорогой путь ограничен одним
    запросом на источник, а список по-прежнему читается из снимка.
    """
    transport = _transport(monkeypatch)
    async with (
        _db() as sm,
        AsyncClient(transport=ASGITransport(app=_app(sm)), base_url="http://test") as client,
    ):
        response = await client.get(
            "/api/backend-users",
            params={"date_from": "2026-08-01", "date_to": "2026-08-19"},
        )

    assert response.status_code == 200
    stats_calls = [(m, p) for m, p in transport.paths if m == "GET" and p.endswith("/stats")]
    assert len(stats_calls) == 2  # ровно по одному на каждый из двух бэков
    # Ни списка, ни карточек вовне не запрашивали. Probe `GET /products` (детекция
    # префикса, ADR-072 §4а) — не «запрос данных»: он уходит один раз на бэк на холодном
    # кэше процесса и в норме §3 не учитывается, поэтому исключается явно.
    non_probe = [(m, p) for m, p in transport.paths if not p.endswith("/products")]
    assert non_probe == stats_calls
    # Периодная сводка пришла от бэков (4 + 4), а не из строк источников (7 + 5).
    assert response.json()["stats"]["users_total"] == 8
