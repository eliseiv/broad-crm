from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

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

    monkeypatch.setattr(prometheus.httpx, "AsyncClient", FakeClient)

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

    monkeypatch.setattr(prometheus.httpx, "AsyncClient", FailingClient)
    client = PrometheusClient("http://prometheus:9090", 10)

    with pytest.raises(PrometheusUnavailable):
        await client.query("http_error")
    with pytest.raises(PrometheusUnavailable):
        await client.query("status_error")


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

    assert await health(BadSession()) == {
        "status": "degraded",
        "db": "down",
        "prometheus": "down",
    }


def test_logging_masks_passwords_tokens_and_keys() -> None:
    event = _mask_secrets(
        None,  # type: ignore[arg-type]
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
