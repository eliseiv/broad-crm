"""⛔ Сессия БД НЕ удерживается на время HTTP-обхода воркера (ADR-080 §2, docstring сервиса).

**Нормативный инвариант.** `BackendUsersSnapshotService` ходит в бэк БЕЗ открытой сессии:
снимок читается короткой сессией в память, дальше идут только сетевые вызовы и короткие
`UPDATE`/`INSERT` (канон `BackendMonitorService.poll_once`). Иначе полный обход крупного
бэка держал бы транзакцию в `idle in transaction` минутами, а `Semaphore(5)` параллельных
бэков при пуле того же порядка выбрал бы пул целиком — интерактивные запросы API уходили
бы в `pool_timeout`. Дефект такого рода **не виден ни по одному функциональному ассерту**:
снимок собирается корректно, тесты зелёные, а прод ложится под нагрузкой.

**Как измеряется.** Пул SQLAlchemy — единственный честный свидетель: во время КАЖДОГО
upstream-запроса счётчик `pool.checkedout()` обязан быть нулём. Замер снимается изнутри
транспорта (`FakeAdminTransport.handle_async_request`), то есть ровно в тот момент, когда
воркер «висит на сети». Регресс `async with self._sessionmaker() as session:` вокруг
обхода поднимет счётчик до 1 и уронит гейт.

Тест интеграционный (реальный Postgres): пул фейком не заменить — считается настоящий
checkout настоящего соединения, а не вызовы к double'у.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
import pytest
from app.infra.backend_admin_client import _clear_prefix_cache
from app.infra.crypto import encrypt_secret
from app.models.service_backend import Backend
from app.services.backend_users_snapshot_service import BackendUsersSnapshotService
from backend_admin_helpers import FakeAdminTransport, install_transport
from mail_s34_helpers import mail_db
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
ADMIN_KEY = "admin-key-of-the-snapshot-source"
USER_ID = "c18ae65b-a6ab-4a1b-a13a-40b4c0d23708"


@pytest.fixture(autouse=True)
def _reset_prefix_cache() -> Iterator[None]:
    """Кэш префиксов клиента — process-global; без сброса тесты зависят от порядка."""
    _clear_prefix_cache()
    yield
    _clear_prefix_cache()


@dataclass
class PoolProbeTransport(FakeAdminTransport):
    """Транспорт-фейк, снимающий число занятых соединений пула В МОМЕНТ HTTP-запроса.

    Замер делается ДО отдачи ответа — то есть в точке, где воркер физически ждёт сеть.
    """

    engine: AsyncEngine | None = None
    checkouts: list[tuple[str, int]] = field(default_factory=list)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert self.engine is not None
        path = request.url.raw_path.decode("ascii").split("?")[0]
        self.checkouts.append((path, self.engine.sync_engine.pool.checkedout()))
        return await super().handle_async_request(request)


@asynccontextmanager
async def _snapshot_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """БД со снимком: чистые таблицы снимка + один бэк-источник с admin-ключом."""
    async with mail_db() as sm:
        async with sm() as session:
            await session.execute(
                sa_text(
                    "TRUNCATE backend_user_snapshots, backend_user_snapshot_sources, "
                    "backends RESTART IDENTITY CASCADE"
                )
            )
            session.add(
                Backend(
                    id=BACKEND_ID,
                    code="veltrio",
                    name="232",
                    domain="https://veltriohub.shop/",
                    admin_api_key_encrypted=encrypt_secret(ADMIN_KEY),
                )
            )
            await session.commit()
        yield sm


def _transport(engine: AsyncEngine) -> PoolProbeTransport:
    """Исправный источник: одна страница списка, сводка и карточка пользователя.

    Одна страница на 1 элемент (< `limit=100`) завершает обход, но проходит ВСЕ шаги
    цикла: список → `DELETE`-разность → `stats` → добор карточки → пересчёт агрегата.

    Правило `GET /products` обязательно: на холодном кэше клиент определяет префикс
    контракта через probe `GET /products` (ADR-072 §4а), и без правила probe получает
    умолчание `404` на обоих кандидатах — обход падает `backend_admin_not_supported`,
    так и не дойдя до сети данных (гейт стал бы вакуумным).
    """
    transport = PoolProbeTransport(engine=engine)
    transport.on("GET", "/products", status=200, json_body={"items": []})
    transport.on(
        "GET",
        "/users",
        status=200,
        json_body={
            "total": 1,
            "items": [
                {
                    "id": USER_ID,
                    "external_id": "EXT-1",
                    "registered_at": "2026-08-13T17:01:14Z",
                    "tokens": 100,
                }
            ],
        },
    )
    transport.on(
        "GET",
        "/stats",
        status=200,
        json_body={"users_total": 1, "paid_users": 0, "payments_sum_usd": 0},
    )
    transport.on(
        "GET",
        f"/users/{USER_ID}",
        status=200,
        json_body={
            "id": USER_ID,
            "registered_at": "2026-08-13T17:01:14Z",
            "revenue": {"api_cost_usd": 0.5, "providers": {"openai": 0.5}},
        },
    )
    return transport


async def test_pool_is_free_during_every_upstream_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Гейт: во время КАЖДОГО HTTP-запроса воркера занятых соединений пула — ноль."""
    from app.config import get_settings

    async with _snapshot_db() as sm:
        engine: AsyncEngine = sm.kw["bind"]

        # Контроль чувствительности замера: открытая сессия с выполненным SQL ОБЯЗАНА
        # быть видна пулу. Без этой проверки гейт мог бы быть зелёным просто потому,
        # что счётчик всегда ноль (не тот пул / не тот движок).
        async with sm() as probe_session:
            await probe_session.execute(sa_text("SELECT 1"))
            assert engine.sync_engine.pool.checkedout() == 1

        transport = _transport(engine)
        install_transport(monkeypatch, transport)

        service = BackendUsersSnapshotService(sessionmaker=sm, settings=get_settings())
        await service.refresh_once()

        # Обход реально состоялся: список, сводка и карточка — минимум три запроса.
        assert len(transport.checkouts) >= 3
        assert any(path.endswith("/stats") for path, _ in transport.checkouts)
        assert any(path.endswith(f"/users/{USER_ID}") for path, _ in transport.checkouts)

        # ⛔ Главный ассерт: ни одного занятого соединения в момент похода в сеть.
        held = [(path, count) for path, count in transport.checkouts if count != 0]
        assert held == [], f"сессия БД удерживалась во время HTTP: {held}"

        # И воркер при этом действительно писал в БД (иначе «ноль checkout'ов» тривиален).
        async with sm() as session:
            rows = await session.execute(
                sa_text("SELECT user_id FROM backend_user_snapshots WHERE backend_id = :b"),
                {"b": str(BACKEND_ID)},
            )
            assert set(rows.scalars().all()) == {USER_ID}
            refreshed = await session.execute(
                sa_text(
                    "SELECT refreshed_at FROM backend_user_snapshot_sources "
                    "WHERE backend_id = :b"
                ),
                {"b": str(BACKEND_ID)},
            )
            assert refreshed.scalar_one() is not None


async def test_pool_stays_free_after_the_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждая короткая сессия закрыта: после цикла соединения возвращены в пул.

    Утечка соединения не ловится первым гейтом (он смотрит только моменты HTTP), но
    приводит к тому же исходу — пул кончается через несколько циклов.
    """
    from app.config import get_settings

    async with _snapshot_db() as sm:
        engine: AsyncEngine = sm.kw["bind"]
        install_transport(monkeypatch, _transport(engine))

        service = BackendUsersSnapshotService(sessionmaker=sm, settings=get_settings())
        await service.refresh_once()
        await service.refresh_once()

        assert engine.sync_engine.pool.checkedout() == 0
