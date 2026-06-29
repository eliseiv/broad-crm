from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from app.api.health import health
from app.infra import file_sd, prometheus
from app.infra.prometheus import PrometheusClient, PrometheusUnavailable
from app.logging import _mask_secrets


def test_file_sd_writes_atomically_and_deletes_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FILE_SD_DIR", str(tmp_path))
    from app.config import get_settings

    get_settings.cache_clear()
    server_id = uuid.uuid4()

    file_sd.write_target(server_id=server_id, ip="10.0.0.10", exporter_port=9100, name="Server 01")
    target = tmp_path / f"{server_id}.json"

    assert target.exists()
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {
            "targets": ["10.0.0.10:9100"],
            "labels": {"server_id": str(server_id), "name": "Server 01"},
        }
    ]

    file_sd.delete_target(server_id)
    assert not target.exists()


@pytest.mark.asyncio
async def test_prometheus_client_parses_success_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {"instance": "10.0.0.10:9100"}, "value": [1, "42"]}],
                },
            }

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, url: str, params: dict[str, str]) -> FakeResponse:
            assert url == "http://prometheus:9090/api/v1/query"
            assert params == {"query": "vector(1)"}
            return FakeResponse()

    monkeypatch.setattr("app.infra.prometheus.httpx.AsyncClient", FakeClient)

    assert await PrometheusClient("http://prometheus:9090", 10).query("vector(1)") == [
        {"metric": {"instance": "10.0.0.10:9100"}, "value": [1, "42"]}
    ]


@pytest.mark.asyncio
async def test_prometheus_client_raises_on_http_and_status_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> FailingClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, _url: str, params: dict[str, str]) -> object:
            if params["query"] == "http_error":
                raise httpx.ConnectError("down")

            class ErrorResponse:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, object]:
                    return {"status": "error", "data": {"resultType": "vector", "result": []}}

            return ErrorResponse()

    monkeypatch.setattr("app.infra.prometheus.httpx.AsyncClient", FailingClient)
    client = PrometheusClient("http://prometheus:9090", 10)

    with pytest.raises(PrometheusUnavailable):
        await client.query("http_error")
    with pytest.raises(PrometheusUnavailable):
        await client.query("status_error")


@pytest.mark.asyncio
async def test_prometheus_client_retries_503_twice_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_sleep(_delay: float) -> None:
        return None

    class RetryClient:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> RetryClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, url: str, params: dict[str, str]) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            request = httpx.Request("GET", url, params=params)
            if attempts < 3:
                return httpx.Response(503, request=request)
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [{"metric": {"instance": "10.0.0.10:9100"}, "value": [1, "1"]}],
                    },
                },
            )

    monkeypatch.setattr("app.infra.prometheus.asyncio.sleep", no_sleep)
    monkeypatch.setattr("app.infra.prometheus.httpx.AsyncClient", RetryClient)

    result = await PrometheusClient("http://prometheus:9090", 10).query("up")

    assert attempts == 3
    assert result == [{"metric": {"instance": "10.0.0.10:9100"}, "value": [1, "1"]}]


@pytest.mark.asyncio
async def test_prometheus_client_does_not_retry_non_429_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    async def no_sleep(_delay: float) -> None:
        pytest.fail("400 must not be retried")

    class BadRequestClient:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> BadRequestClient:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def get(self, url: str, params: dict[str, str]) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(400, request=httpx.Request("GET", url, params=params))

    monkeypatch.setattr("app.infra.prometheus.asyncio.sleep", no_sleep)
    monkeypatch.setattr("app.infra.prometheus.httpx.AsyncClient", BadRequestClient)

    with pytest.raises(PrometheusUnavailable):
        await PrometheusClient("http://prometheus:9090", 10).query("bad promql")

    assert attempts == 1


@pytest.mark.asyncio
async def test_health_reports_degraded_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadSession:
        async def execute(self, _query: object) -> None:
            raise RuntimeError("db down")

    class BadPrometheus:
        async def query(self, _query: str) -> list[dict[str, Any]]:
            raise PrometheusUnavailable("prom down")

    monkeypatch.setattr(prometheus, "get_prometheus_client", lambda: BadPrometheus())
    monkeypatch.setattr("app.api.health.get_prometheus_client", lambda: BadPrometheus())

    assert await health(cast(Any, BadSession())) == {
        "status": "degraded",
        "db": "down",
        "prometheus": "down",
    }


def test_logging_masks_passwords_tokens_and_keys() -> None:
    event = _mask_secrets(
        None,
        "info",
        {
            "password": "plain",
            "access_token": "jwt",
            "fernet_key": "key",
            "safe": "value",
        },
    )

    assert event == {
        "password": "***",
        "access_token": "***",
        "fernet_key": "***",
        "safe": "value",
    }
