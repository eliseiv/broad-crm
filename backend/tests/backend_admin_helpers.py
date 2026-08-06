"""Фейк upstream-транспорта CRM Admin API бэков (contract v1 + расширение v1.1, ADR-072).

Вспомогательный модуль (без `test_`-префикса — pytest его не коллектит), общий для
unit-тестов клиента (`tests/unit/test_backend_admin_client.py`) и интеграционных тестов
роутера «Продукты и тарифы» (`tests/integration/test_backend_economics_api.py`).

**Почему подмена идёт на уровне ТРАНСПОРТА, а не через `app.dependency_overrides`.**
`BackendAdminClient` создаётся ПРЯМЫМ вызовом внутри `BackendAdminSourceResolver.client()`
(`app/services/backend_admin_source.py:53`), а не резолвится через `Depends`, поэтому
override на сам клиент не перехватывается ничем и тест был бы зелёным молча. Здесь
подменяется фабрика `httpx.AsyncClient` внутри модуля клиента — **все kwargs пробрасываются
как есть** (`headers` с `X-Admin-Key`, `follow_redirects=False`, per-phase `timeout`), иначе
проверки заголовков и ветки `redirect` стали бы фиктивными.

Записываются ЦЕЛИКОМ объекты `httpx.Request`: тестам нужен `url.raw_path` (сырой,
неразобранный путь) — `url.path` httpx декодирует, и регресс-гейт на экранирование
`product_id` по нему не ловится (`_segment`, `backend_admin_client.py:79-89`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

# Сентинел «тело не задано» — `None`, `[]`, `0` сами по себе валидные тела ответа.
_UNSET = object()

# Настоящий класс клиента, снятый ДО любых подмен: `install_transport` может вызываться
# в одном тесте повторно (другой набор правил), и брать `httpx.AsyncClient` в момент
# подмены нельзя — вторая установка обернула бы уже обёрнутую фабрику и упала бы на
# дублирующемся аргументе `transport`.
_REAL_ASYNC_CLIENT = httpx.AsyncClient


@dataclass
class Rule:
    """Программируемый ответ бэка на конкретном (метод, суффикс пути).

    `exc` (если задан) поднимается ВМЕСТО ответа — так моделируются исходы без ответа
    (`httpx.TimeoutException` → `reason=timeout`, `httpx.ConnectError` → `reason=transport`).
    `text_body` отдаёт сырое тело (для `bad_json`), `json_body` — JSON любого типа
    (`[]`/`"ok"` → `schema_mismatch`).
    """

    status: int = 404
    json_body: Any = _UNSET
    text_body: str | None = None
    exc: Exception | None = None
    headers: dict[str, str] | None = None


@dataclass
class FakeAdminTransport(httpx.AsyncBaseTransport):
    """Транспорт-фейк admin-эндпоинтов бэка: маршруты по суффиксу пути + журнал запросов.

    Маршрут матчится по `(METHOD, суффикс пути)`; первый подошедший выигрывает. Суффикс
    сравнивается с СЫРЫМ путём (`raw_path`), чтобы правило нельзя было задеть кодированным
    сегментом. Умолчание — `404` без тела: это и есть «префикса тут нет», на чём работает
    детекция префикса (`PREFIX_CANDIDATES`, ADR-072 §4а).
    """

    rules: list[tuple[str, str, Rule]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    default: Rule = field(default_factory=Rule)

    def on(
        self,
        method: str,
        path_suffix: str,
        *,
        status: int = 200,
        json_body: Any = _UNSET,
        text_body: str | None = None,
        exc: Exception | None = None,
        headers: dict[str, str] | None = None,
    ) -> FakeAdminTransport:
        """Регистрирует правило (fluent — вызовы цепляются).

        Правило кладётся В НАЧАЛО: ПОЗЖЕ зарегистрированное ПЕРЕКРЫВАЕТ раньшее. Иначе
        тест, уточняющий один маршрут поверх общей «исправной» фикстуры, молча получал бы
        ответ фикстуры — и был бы зелёным, ничего не проверив.
        """
        self.rules.insert(
            0,
            (
                method.upper(),
                path_suffix,
                Rule(
                    status=status,
                    json_body=json_body,
                    text_body=text_body,
                    exc=exc,
                    headers=headers,
                ),
            ),
        )
        return self

    # --- журнал запросов ---

    @property
    def paths(self) -> list[tuple[str, str]]:
        """`(METHOD, сырой путь)` каждого ушедшего запроса — в порядке отправки."""
        return [(r.method, r.url.raw_path.decode("ascii").split("?")[0]) for r in self.requests]

    @property
    def queries(self) -> list[str]:
        """Строка query каждого запроса (для ассерта `scope=all` / его отсутствия)."""
        return [r.url.query.decode("ascii") for r in self.requests]

    def queries_for(self, method: str, path_suffix: str) -> list[str]:
        """Строки query запросов на конкретный путь — в порядке отправки.

        Индексировать общий журнал нельзя: за одним обработчиком CRM идёт НЕСКОЛЬКО
        upstream-запросов (список + необязательный `/capabilities`), и «последний запрос»
        принадлежит не тому пути, который проверяется.
        """
        return [
            r.url.query.decode("ascii")
            for r in self.requests
            if r.method == method.upper()
            and r.url.raw_path.decode("ascii").split("?")[0].endswith(path_suffix)
        ]

    def _match(self, request: httpx.Request) -> Rule:
        raw_path = request.url.raw_path.decode("ascii").split("?")[0]
        for method, suffix, rule in self.rules:
            if request.method == method and raw_path.endswith(suffix):
                return rule
        return self.default

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        rule = self._match(request)

        if rule.exc is not None:
            raise rule.exc

        if rule.text_body is not None:
            return httpx.Response(rule.status, text=rule.text_body, headers=rule.headers)
        if rule.json_body is _UNSET:
            return httpx.Response(rule.status, headers=rule.headers)
        return httpx.Response(rule.status, json=rule.json_body, headers=rule.headers)


def install_transport(monkeypatch: Any, transport: FakeAdminTransport) -> None:
    """Подменяет фабрику `httpx.AsyncClient` внутри `app.infra.backend_admin_client`.

    Все kwargs прод-кода (`app/infra/backend_admin_client.py:396-403`) пробрасываются без
    изменений — иначе `X-Admin-Key`/`X-Admin-Actor` и `follow_redirects=False` не дошли бы
    до фейка и соответствующие ассерты ничего бы не проверяли.
    """
    import app.infra.backend_admin_client as mod

    def _factory(**kwargs: Any) -> httpx.AsyncClient:
        return _REAL_ASYNC_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _factory)


class RecordingLogger:
    """Детерминированный перехват structlog-событий подменой module-логгера.

    `structlog.testing.capture_logs()` в этом репо флейкает из-за
    `cache_logger_on_first_use=True` (см. `tests/integration/test_secret_reveal.py:74`),
    поэтому события снимаются подменой `logger` конкретного модуля.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def error(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def debug(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def named(self, name: str) -> list[dict[str, Any]]:
        """Все поля событий с данным именем (в порядке записи)."""
        return [fields for event, fields in self.events if event == name]

    def contains_value(self, needle: str) -> bool:
        """Есть ли `needle` в ЛЮБОМ поле ЛЮБОГО события (гейт «секрет не в логах»)."""
        return any(needle in repr(fields) or needle in event for event, fields in self.events)
