"""Фейк httpx-транспорта агрегатора для тестов бюджетов почты (ADR-053).

Вспомогательный модуль (без `test_`-префикса — pytest не коллектит), общий для unit-тестов
транспорта (`tests/unit/test_mail_client_timeouts.py`) и интеграционных тестов эндпоинтов
(`tests/integration/test_mail_timeouts_api.py`).

Зачем СВОЙ транспорт, а не `httpx.MockTransport`: `MockTransport` НЕ применяет таймауты
(он не ходит в сеть), поэтому «агрегатор молчит дольше read-бюджета» на нём не моделируется.
Здесь транспорт читает фактический per-phase бюджет из `request.extensions["timeout"]` —
тот самый `httpx.Timeout`, который собирает `MailClient` (`mail_client.py:274-279`), — и
ведёт себя как настоящий: ждёт не дольше `read`, после чего поднимает `httpx.ReadTimeout`.
Побочно это даёт ассерт «таймаут задан ПО ФАЗАМ, а не одиночным float» (ADR-053 §1.2).

Бюджеты в тестах — СУБ-СЕКУНДНЫЕ и инъектируются в КОНСТРУКТОР клиента
(`read_timeout_sec`/`deadline_sec`), а НЕ через `Settings` (там `int` + `ge/le`, суб-секундные
значения недостижимы) — нормативно, 06-testing-strategy.md §Интеграционные.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class Rule:
    """Программируемое поведение агрегатора на конкретном маршруте."""

    status: int = 200
    json_body: dict[str, Any] | None = None
    # Задержка ответа. Больше read-бюджета → транспорт поднимет ReadTimeout (как настоящий).
    delay_sec: float = 0.0
    # Первые N попыток — `ConnectError` (соединение не установлено → штатный ретрай ADR-038 §1).
    connect_errors: int = 0
    attempts: int = 0


@dataclass
class FakeAggregatorTransport(httpx.AsyncBaseTransport):
    """Транспорт-фейк агрегатора: маршруты, задержки, connect-ошибки; уважает read-бюджет.

    Маршрут матчится по `(METHOD, суффикс пути)`; первый подошедший выигрывает. Без правил —
    `200 {}`. Все запросы и их per-phase бюджеты записываются (`calls` / `timeouts`).
    """

    rules: list[tuple[str, str, Rule]] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    timeouts: list[dict[str, float | None]] = field(default_factory=list)
    default: Rule = field(default_factory=Rule)

    def on(
        self,
        method: str,
        path_suffix: str,
        *,
        status: int = 200,
        json_body: dict[str, Any] | None = None,
        delay_sec: float = 0.0,
        connect_errors: int = 0,
    ) -> FakeAggregatorTransport:
        self.rules.append(
            (
                method.upper(),
                path_suffix,
                Rule(
                    status=status,
                    json_body=json_body,
                    delay_sec=delay_sec,
                    connect_errors=connect_errors,
                ),
            )
        )
        return self

    def _match(self, request: httpx.Request) -> Rule:
        for method, suffix, rule in self.rules:
            if request.method == method and request.url.path.endswith(suffix):
                return rule
        return self.default

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        timeout: dict[str, float | None] = dict(request.extensions.get("timeout", {}))
        self.timeouts.append(timeout)

        rule = self._match(request)
        rule.attempts += 1

        if rule.attempts <= rule.connect_errors:
            raise httpx.ConnectError("connection refused", request=request)

        if rule.delay_sec > 0:
            read_budget = timeout.get("read")
            if read_budget is not None and rule.delay_sec > read_budget:
                # Настоящий транспорт ждёт ровно read-бюджет и падает ReadTimeout.
                await asyncio.sleep(read_budget)
                raise httpx.ReadTimeout("read timeout", request=request)
            await asyncio.sleep(rule.delay_sec)

        return httpx.Response(
            rule.status, json=rule.json_body if rule.json_body is not None else {}
        )


def install_transport(monkeypatch: Any, transport: FakeAggregatorTransport) -> None:
    """Подменяет `httpx.AsyncClient` внутри `app.infra.mail_client` на клиент с фейк-транспортом.

    `MailClient` создаёт `AsyncClient` сам (`mail_client.py:281`), поэтому транспорт
    инъектируется подменой фабрики. `timeout` ПРОБРАСЫВАЕТСЯ как есть — иначе фейк не увидел
    бы per-phase бюджет и проверка read-таймаута стала бы фиктивной.
    """
    import app.infra.mail_client as mod

    real_client = httpx.AsyncClient

    def _factory(*_args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=transport, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(mod.httpx, "AsyncClient", _factory)


def error_body(code: str) -> dict[str, Any]:
    """Тело ошибки агрегатора в его едином формате (из него берётся только `error.code`)."""
    return {"error": {"code": code, "message": "aggregator says no"}}
