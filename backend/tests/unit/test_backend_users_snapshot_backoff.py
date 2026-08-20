"""Бережность воркера снимка к источнику: backoff на 429/5xx, троттлинг, break (Q-BU-2).

Прод-инцидент 2026-08-20: воркер обходил до ~3050 страниц `GET {P}/users` подряд без пауз
и без ретраев, при fan-out `Semaphore(5)` на бэки-соседи по одному серверу. Источники
уходили в `429`/`500`, цикл падал, `refreshed_at` не проставлялся НИКОГДА — «Снимок
формируется…» висел часами, а `errors[]` заполнялся «Ошибка бэка (HTTP 429/500)».

Гейты здесь идут через НАСТОЯЩИЙ `BackendAdminClient` поверх `FakeAdminTransport`: ретрай
опирается на машинный статус ответа (`BackendAdminUpstreamStatus`) и на `Retry-After`, и
проверять это на самодельном фейке клиента значило бы проверять сам фейк.

Пауз в реальном времени тесты не выдерживают: `asyncio.sleep` модуля сервиса подменён
записывающим фейком — он же служит ассертом (какие именно задержки запрошены).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from app.infra.backend_admin_client import BackendAdminClient, _clear_prefix_cache
from app.services import backend_users_snapshot_service as svc
from app.services.backend_users_snapshot_service import BackendUsersSnapshotService
from backend_admin_helpers import FakeAdminTransport, Rule, install_transport

BACKEND_ID = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
DOMAIN = "https://veltriohub.shop/"
ADMIN_KEY = "admin-key-of-the-snapshot-source"


@pytest.fixture(autouse=True)
def _reset_prefix_cache() -> Any:
    """Кэш префиксов клиента — process-global; без сброса тесты зависят от порядка."""
    _clear_prefix_cache()
    yield
    _clear_prefix_cache()


# --- Инфраструктура фейков ---------------------------------------------------


class _FakeRepo:
    """In-memory репозиторий снимка: ровно тот интерфейс, которым пользуется воркер."""

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


def _service(
    state: dict[str, Any],
    *,
    revenue_batch: int = 2000,
    page_delay_sec: float = 0.0,
    retry_attempts: int = 5,
    retry_base_sec: float = 1.0,
    retry_cap_sec: float = 30.0,
    concurrency: int = 2,
) -> BackendUsersSnapshotService:
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={
            "backend_users_snapshot_revenue_batch": revenue_batch,
            "backend_users_snapshot_page_delay_sec": page_delay_sec,
            "backend_users_snapshot_retry_attempts": retry_attempts,
            "backend_users_snapshot_retry_base_sec": retry_base_sec,
            "backend_users_snapshot_retry_cap_sec": retry_cap_sec,
            "backend_users_snapshot_concurrency": concurrency,
        }
    )
    service = BackendUsersSnapshotService(
        sessionmaker=lambda: None,  # type: ignore[arg-type]
        settings=settings,
    )

    @asynccontextmanager
    async def _in_session() -> Any:
        yield _FakeRepo(state)

    service._in_session = _in_session  # type: ignore[method-assign]
    return service


@dataclass
class ScriptedTransport(FakeAdminTransport):
    """`FakeAdminTransport` + ПОСЛЕДОВАТЕЛЬНОСТЬ ответов на `GET {P}/users`.

    Обычные правила статичны, а гейт «429 → повтор → успех» требует, чтобы ОДИН и тот же
    запрос отвечал по-разному во времени. Скрипт вычерпывается по одному ответу на запрос;
    когда он исчерпан, работают обычные правила.
    """

    script: list[Rule] | None = None

    def _match(self, request: httpx.Request) -> Rule:
        raw_path = request.url.raw_path.decode("ascii").split("?")[0]
        if request.method == "GET" and raw_path.endswith("/users") and self.script:
            return self.script.pop(0)
        return super()._match(request)


def _user(index: int) -> dict[str, Any]:
    return {"id": f"u{index:05d}", "registered_at": "2026-08-13T17:01:14Z"}


def _page(users: list[dict[str, Any]]) -> Rule:
    return Rule(status=200, json_body={"total": len(users), "items": users})


def _healthy_transport(script: list[Rule] | None = None) -> ScriptedTransport:
    """Исправный источник: probe префикса, сводка и карточка любого пользователя.

    Правило `GET /products` обязательно: на холодном кэше клиент определяет префикс
    контракта именно этим probe (ADR-072 §4а), иначе обход падает `not_supported`, так и
    не дойдя до проверяемого поведения — гейт стал бы вакуумным.
    """
    transport = ScriptedTransport(script=script)
    transport.on("GET", "/products", status=200, json_body={"items": []})
    transport.on(
        "GET",
        "/stats",
        status=200,
        json_body={"users_total": 1, "paid_users": 0, "payments_sum_usd": 0},
    )
    return transport


def _client() -> BackendAdminClient:
    return BackendAdminClient(BACKEND_ID, DOMAIN, ADMIN_KEY)


def _capture_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Подменяет `asyncio.sleep` воркера: пауз в реальном времени в тестах нет."""
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(svc.asyncio, "sleep", _fake_sleep)
    return sleeps


def _user_page_requests(transport: FakeAdminTransport) -> list[tuple[str, str]]:
    return [(m, p) for m, p in transport.paths if p.endswith("/users")]


# --- 1. Ретрай на 429: цикл доходит до конца и проставляет refreshed_at -------


async def test_rate_limited_page_is_retried_and_cycle_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`429` на странице обхода — временный отказ: повтор, и цикл завершается успешно.

    Гейт прод-дефекта: без повтора цикл падал на первом же `429`, `refreshed_at` не
    проставлялся, и следующий цикл падал там же — «Снимок формируется…» навсегда.
    """
    sleeps = _capture_sleeps(monkeypatch)
    state = _state()
    transport = _healthy_transport(
        script=[
            Rule(status=429, headers={"Retry-After": "2"}),
            _page([_user(0)]),
        ]
    )
    transport.on(
        "GET",
        "/users/u00000",
        status=200,
        json_body={
            "id": "u00000",
            "registered_at": "2026-08-13T17:01:14Z",
            "revenue": {"api_cost_usd": 0.5, "providers": {"openai": 0.5}},
        },
    )
    install_transport(monkeypatch, transport)

    service = _service(state)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    # Страница действительно запрошена дважды: отказ → повтор.
    assert len(_user_page_requests(transport)) == 2
    # Цикл успешен: свежесть проставлена, сбой не записан.
    assert state["source"][-1]["refreshed_at"] is not None
    assert state["source"][-1]["error_message"] is None
    assert state["revenue_written"] == ["u00000"]
    # `Retry-After` бэка уважён как есть (потолок 30 с не задет).
    assert sleeps == [2.0]


async def test_server_error_on_stats_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ретраи покрывают ВЕСЬ цикл, а не только обход: `5xx` на сводке тоже повторяется."""
    sleeps = _capture_sleeps(monkeypatch)
    state = _state()
    transport = _healthy_transport(script=[_page([])])
    # Позже зарегистрированное правило перекрывает раньшее (см. `FakeAdminTransport.on`).
    transport.on("GET", "/stats", status=500)
    install_transport(monkeypatch, transport)

    service = _service(state, retry_attempts=3, revenue_batch=0)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    stats_calls = [(m, p) for m, p in transport.paths if p.endswith("/stats")]
    assert len(stats_calls) == 3  # исчерпаны все попытки
    assert len(sleeps) == 2  # пауз на одну меньше, чем попыток
    assert state["source"][-1]["error_message"]


# --- 2. Исчерпание ретраев: прежнее поведение отказа --------------------------


async def test_retry_exhaustion_fails_the_cycle_and_keeps_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Постоянный `500`: цикл бэка падает, снимок прошлого цикла ЦЕЛ (§2 п.7).

    Ретраи не меняют контракта отказа — они лишь дают источнику шанс. Снимок не
    прореживается, `refreshed_at` не затирается, причина видна в `errors[]`.
    """
    sleeps = _capture_sleeps(monkeypatch)
    state = _state(fingerprints={"u00000": (), "u99999": ()})
    transport = _healthy_transport(script=[Rule(status=500) for _ in range(10)])
    install_transport(monkeypatch, transport)

    service = _service(state, retry_attempts=4)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    assert len(_user_page_requests(transport)) == 4  # ровно `retry_attempts`
    assert len(sleeps) == 3
    assert state["deleted"] == []  # снимок не прорежен
    assert state["upserted"] == []
    failure = state["source"][-1]
    assert failure["error_message"] == "Ошибка бэка (HTTP 500)"
    assert failure["failed_at"] is not None
    assert "refreshed_at" not in failure  # прошлая метка свежести не затёрта


async def test_permanent_status_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """`401` (отвергнутый admin-ключ) — постоянный отказ: повторять нечего.

    Гейт «ретрай не тащит всё подряд»: лишние повторы на отвергнутом ключе только
    добавляют нагрузки источнику, который отвечает исправно.
    """
    sleeps = _capture_sleeps(monkeypatch)
    state = _state()
    transport = _healthy_transport(script=[Rule(status=401) for _ in range(5)])
    install_transport(monkeypatch, transport)

    service = _service(state, retry_attempts=5)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    assert len(_user_page_requests(transport)) == 1
    assert sleeps == []
    assert state["source"][-1]["failed_at"] is not None


async def test_backoff_is_exponential_and_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без `Retry-After` задержка растёт как `base * 2^attempt` и упирается в `cap`.

    Джиттер (половина окна) проверяется границами, а не точным значением: точное равенство
    требовало бы подмены `random` и проверяло бы уже не поведение, а фейк.
    """
    sleeps = _capture_sleeps(monkeypatch)
    state = _state()
    transport = _healthy_transport(script=[Rule(status=429) for _ in range(10)])
    install_transport(monkeypatch, transport)

    service = _service(state, retry_attempts=6, retry_base_sec=1.0, retry_cap_sec=8.0)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    assert len(sleeps) == 5
    for attempt, delay in enumerate(sleeps):
        window = min(1.0 * (2.0**attempt), 8.0)
        assert window / 2 <= delay <= window
    assert sleeps[-1] <= 8.0  # потолок соблюдён


# --- 3. Троттлинг между страницами и между карточками ------------------------


async def test_pause_between_pages_of_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Между страницами обхода выдерживается пауза `page_delay_sec`."""
    sleeps = _capture_sleeps(monkeypatch)
    state = _state()
    full_page = [_user(i) for i in range(100)]  # ровно `_SOURCE_PAGE_LIMIT` ⇒ обход продолжится
    transport = _healthy_transport(script=[_page(full_page), _page([_user(100)])])
    install_transport(monkeypatch, transport)

    service = _service(state, page_delay_sec=0.25, revenue_batch=0)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    assert len(_user_page_requests(transport)) == 2
    # Ровно одна пауза — ПЕРЕД второй страницей, а не перед первой (первая ничего не ждёт).
    assert sleeps == [0.25]
    assert state["source"][-1]["refreshed_at"] is not None


async def test_pause_between_user_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Между карточками `GET {P}/users/{id}` — та же пауза: их за цикл до `revenue_batch`."""
    sleeps = _capture_sleeps(monkeypatch)
    state = _state()
    transport = _healthy_transport(script=[_page([_user(0), _user(1), _user(2)])])
    for index in range(3):
        transport.on(
            "GET",
            f"/users/u{index:05d}",
            status=200,
            json_body={"id": f"u{index:05d}", "registered_at": "2026-08-13T17:01:14Z"},
        )
    install_transport(monkeypatch, transport)

    service = _service(state, page_delay_sec=0.25, revenue_batch=3)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    assert state["revenue_written"] == ["u00000", "u00001", "u00002"]
    assert sleeps == [0.25, 0.25]  # три карточки — две паузы МЕЖДУ ними


async def test_zero_delay_disables_throttling(monkeypatch: pytest.MonkeyPatch) -> None:
    """`page_delay_sec = 0` — троттлинг выключен полностью (прежняя скорость обхода)."""
    sleeps = _capture_sleeps(monkeypatch)
    state = _state()
    full_page = [_user(i) for i in range(100)]
    transport = _healthy_transport(script=[_page(full_page), _page([_user(100)])])
    install_transport(monkeypatch, transport)

    service = _service(state, page_delay_sec=0.0, revenue_batch=0)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    assert sleeps == []


# --- 4. Конкурентность fan-out — из конфига ----------------------------------


class _FakeResolver:
    """Резолвер источников: N бэков с admin-ключом (клиент не понадобится — обход фейковый)."""

    targets = 6

    def __init__(self, _repo: Any) -> None:
        pass

    async def list_with_admin_key(self) -> list[tuple[Any, Any]]:
        return [
            (SimpleNamespace(id=uuid.UUID(int=i), code=f"b{i}"), object())
            for i in range(self.targets)
        ]


@pytest.mark.parametrize("concurrency", [1, 2, 4])
async def test_fanout_concurrency_comes_from_config(
    monkeypatch: pytest.MonkeyPatch, concurrency: int
) -> None:
    """Одновременно опрашивается не больше `BACKEND_USERS_SNAPSHOT_CONCURRENCY` бэков.

    Гейт прод-дефекта: жёсткий `Semaphore(5)` дожимал бэки-соседи по одному серверу до
    `429`/`500`. Замер — пик реально одновременных обходов, а не аргумент конструктора
    семафора: подмена конструктора проверяла бы вызов, а не поведение.
    """
    monkeypatch.setattr(svc, "BackendRepository", lambda _session: None)
    monkeypatch.setattr(svc, "BackendAdminSourceResolver", _FakeResolver)

    @asynccontextmanager
    async def _dummy_session() -> Any:
        yield None

    service = _service(_state(), concurrency=concurrency)
    service._sessionmaker = _dummy_session  # type: ignore[assignment]

    in_flight = 0
    peak = 0

    async def _fake_refresh(_backend_id: uuid.UUID, _code: str, _client: Any) -> None:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        for _ in range(3):  # даём планировщику шанс запустить остальные корутины
            await asyncio.sleep(0)
        in_flight -= 1

    service._refresh_backend = _fake_refresh  # type: ignore[method-assign]
    await service.refresh_once()

    assert peak == concurrency


# --- 5. Break на странице без единого нового user_id (Q-BU-2, вариант «а») ----


async def test_stalled_walk_breaks_and_is_recorded_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Источник со сбитой пагинацией: обход оборван И записан как СБОЙ, а не как успех.

    Два гейта в одном, и второй важнее первого.

    (1) Обрыв: без признака бесполезности страницы цикл упирался бы только в `_MAX_PAGES`
        (50 000) — до пяти миллионов запросов к одному бэку за итерацию.

    (2) Оборванный обход НЕ вправе прореживать снимок (инвариант ADR-080 §2 п.3). Здесь
        в снимке 250 живых пользователей, а повторяющееся окно источника показывает лишь
        первую сотню: если оборванный обход считать успешным, разность `known - tracked`
        объявит 150 живых пользователей «исчезнувшими» и `DELETE` их вычистит, а строка
        источника получит `refreshed_at` без единого признака беды. Поэтому fingerprints
        здесь НЕПУСТЫ — на пустом снимке потеря данных не проявилась бы вовсе.
    """
    _capture_sleeps(monkeypatch)
    stuck_page = [_user(i) for i in range(100)]  # полная страница ⇒ обход не завершится сам
    state = _state(fingerprints={f"u{i:05d}": () for i in range(250)})
    transport = _healthy_transport(script=[_page(stuck_page) for _ in range(50)])
    install_transport(monkeypatch, transport)

    service = _service(state, revenue_batch=0)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    # (1) Первая страница обработана, вторая распознана как холостая — и обход оборван.
    assert len(_user_page_requests(transport)) == 2

    # (2) Снимок ЦЕЛ: ни одна строка не удалена, хотя обход видел лишь 100 из 250.
    assert state["deleted"] == []
    failure = state["source"][-1]
    assert failure["error_message"] == "Источник повторяет страницу — обход прерван"
    assert failure["failed_at"] is not None
    assert "refreshed_at" not in failure  # цикл НЕ успешен — свежесть не проставлена


async def test_complete_walk_still_deletes_vanished_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Позитивный контроль: штатный полный обход по-прежнему прореживает снимок.

    Без этого гейта защиту из теста выше можно было бы «починить», отключив `DELETE`
    вовсе, — и потеря функции осталась бы незамеченной.
    """
    _capture_sleeps(monkeypatch)
    state = _state(fingerprints={"u00000": (), "u00001": (), "u99999": ()})
    transport = _healthy_transport(script=[_page([_user(0), _user(1)])])
    install_transport(monkeypatch, transport)

    service = _service(state, revenue_batch=0)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    assert state["deleted"] == ["u99999"]  # исчезнувший — и только он
    assert state["source"][-1]["refreshed_at"] is not None
    assert state["source"][-1]["error_message"] is None


# --- 6. Отказ карточки НЕ роняет цикл (прод-инцидент 2026-08-20) ---------------


async def test_failing_user_card_does_not_block_the_whole_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ Гейт: `500` на карточке оставляет цикл успешным, но помечает расходы неполными.

    На проде несколько бэков стабильно отдавали `500` на карточку ОТДЕЛЬНЫХ
    пользователей, тогда как список, `/stats` и `/products` у них исправны. Пока отказ
    карточки ронял цикл, эти источники не собирали снимок НИКОГДА: строки списка уже
    были верны, но `refreshed_at` не проставлялся, и вся страница показывала «Снимок
    формируется…».

    Фаза экономики вторична по отношению к списку: её отказ означает «расходы неполны»,
    а не «список недостоверен». Поэтому цикл завершается, а неполнота уходит в UI
    флагом `partial` — `revenue_backfill_done` НЕ выставляется.
    """
    _capture_sleeps(monkeypatch)
    state = _state()
    transport = _healthy_transport(script=[_page([_user(0)])])
    transport.on("GET", "/users/u00000", status=500)
    install_transport(monkeypatch, transport)

    service = _service(state)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    written = state["source"][-1]
    # Снимок собран: список верен, свежесть проставлена, сбой источника не записан.
    assert written["refreshed_at"] is not None
    assert written["error_message"] is None
    # Но экономика честно помечена неполной — иначе `partial` погас бы при заниженной сумме.
    assert written["revenue_backfill_done"] is False
    assert state["revenue_written"] == []


async def test_list_walk_failure_still_fails_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обратная сторона: отказ ОБХОДА СПИСКА по-прежнему роняет цикл.

    Разделение принципиально: неполный список нельзя пускать в `delete_rows` — разность
    «было − встречено» объявила бы пропавшими всех, до кого обход не добрался.
    """
    _capture_sleeps(monkeypatch)
    state = _state()
    transport = _healthy_transport(script=[Rule(status=500) for _ in range(12)])
    install_transport(monkeypatch, transport)

    service = _service(state)
    await service._refresh_backend(BACKEND_ID, "veltrio", _client())

    failure = state["source"][-1]
    assert "refreshed_at" not in failure
    assert failure["failed_at"] is not None
