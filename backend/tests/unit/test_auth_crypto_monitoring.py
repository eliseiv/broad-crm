from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.config import get_settings
from app.domain.thresholds import usage_to_zone
from app.errors import AppError
from app.infra.crypto import CryptoError, decrypt_password, encrypt_password
from app.infra.jwt import TokenError, decode_access_token, issue_access_token
from app.infra.rate_limit import InMemoryRateLimiter
from app.services import monitoring_service as monitoring_module
from app.services.auth_service import AuthService
from app.services.monitoring_service import (
    MonitoringService,
    _build_metrics,
    _cpu_detail,
    _instance_matcher,
)
from conftest import RbacFakeDb


@pytest.fixture(autouse=True)
def clear_monitoring_cache() -> None:
    monitoring_module._cache.clear()
    monitoring_module._inflight.clear()


async def test_auth_valid_and_invalid_credentials_expected_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    service = AuthService(
        settings=settings,
        rate_limiter=InMemoryRateLimiter(max_attempts=10, window_sec=300),
        user_repository=RbacFakeDb().user_repo,
    )

    token = await service.login(username="admin", password="secret", client_ip="10.0.0.1")

    assert token.token_type == "bearer"
    # ADR-021: decode_access_token возвращает AccessTokenClaims, а не голый str.
    assert decode_access_token(token.access_token).sub == "admin"

    for username, password in [("admin", "bad"), ("missing", "secret")]:
        with pytest.raises(AppError) as exc:
            await service.login(username=username, password=password, client_ip="10.0.0.2")
        assert exc.value.status_code == 401
        assert exc.value.code == "invalid_credentials"
        assert exc.value.message == "Неверный логин или пароль"

    calls: list[tuple[bytes, bytes]] = []

    def spy_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr("app.services.auth_service.secrets.compare_digest", spy_compare)
    with pytest.raises(AppError):
        await service.login(username="missing", password="bad", client_ip="10.0.0.3")

    # Оба сравнения супер-админа выполняются всегда (constant-time, без раннего возврата);
    # сравниваются UTF-8 БАЙТЫ (ADR-021 Cyrillic login — secrets.compare_digest на не-ASCII
    # str бросает TypeError, поэтому обе стороны кодируются в bytes).
    assert calls == [(b"missing", b"admin"), (b"bad", b"secret")]


async def test_auth_rate_limit_returns_429() -> None:
    service = AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=2, window_sec=300),
        user_repository=RbacFakeDb().user_repo,
    )

    for _ in range(2):
        with pytest.raises(AppError):
            await service.login(username="admin", password="bad", client_ip="10.0.0.9")

    with pytest.raises(AppError) as exc:
        await service.login(username="admin", password="secret", client_ip="10.0.0.9")

    assert exc.value.status_code == 429
    assert exc.value.code == "rate_limited"


def test_jwt_missing_invalid_and_expired_tokens_are_rejected() -> None:
    # ADR-021: issue_access_token — keyword-only sub/role/superadmin/uid.
    token, expires_in = issue_access_token(sub="admin", role="admin", superadmin=True)

    assert expires_in == 86400
    assert decode_access_token(token).sub == "admin"

    with pytest.raises(TokenError):
        decode_access_token("")

    expired = jwt.encode(
        {
            "sub": "admin",
            "type": "access",
            "iat": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        },
        get_settings().jwt_secret,
        algorithm=get_settings().jwt_algorithm,
    )
    with pytest.raises(TokenError):
        decode_access_token(expired)


def test_crypto_roundtrip_wrong_key_and_ciphertext_not_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plaintext = "ssh-secret-password"
    ciphertext = encrypt_password(plaintext)

    assert ciphertext != plaintext.encode("utf-8")
    assert plaintext.encode("utf-8") not in ciphertext
    assert decrypt_password(ciphertext) == plaintext

    monkeypatch.setenv("FERNET_KEY", "vCcoEZzCH1p81RYxE7XTHOUFzThYa1F-0QDoxK46xwc=")
    get_settings.cache_clear()
    with pytest.raises(CryptoError):
        decrypt_password(ciphertext)


def test_usage_to_zone_boundaries() -> None:
    assert usage_to_zone(79.9) == "green"
    assert usage_to_zone(80) == "yellow"
    assert usage_to_zone(90) == "yellow"
    assert usage_to_zone(90.1) == "red"


def test_monitoring_cpu_detail_fallbacks() -> None:
    instance = "10.0.0.1:9100"

    cores = _cpu_detail(instance, cores={instance: 8})
    unknown = _cpu_detail(instance, cores={})

    assert cores.model_dump() == {"value": None, "total": 8, "unit": "cores"}
    assert unknown.model_dump() == {"value": None, "total": None, "unit": "cores"}


def test_monitoring_instance_matcher_exact_and_promql_safe_regex() -> None:
    one = _instance_matcher(["37.27.192.211:9100"])
    many = _instance_matcher(["37.27.192.211:9100", "10.0.0.12:9100"])

    assert one == 'instance="37.27.192.211:9100"'
    assert "\\" not in one
    assert many.startswith('instance=~"')
    assert "37\\\\.27\\\\.192\\\\.211:9100" in many
    assert "10\\\\.0\\\\.0\\\\.12:9100" in many
    assert "\\." not in many.replace("\\\\.", "")


def test_monitoring_build_metrics_maps_promql_values_to_schema() -> None:
    instance = "10.0.0.1:9100"
    gib = 1024**3
    metrics = _build_metrics(
        {
            "cpu_usage": {instance: 65.04},
            "cpu_cores": {instance: 8},
            "ram_usage": {instance: 80},
            "ram_used": {instance: 11.5 * gib},
            "ram_total": {instance: 16 * gib},
            "ssd_usage": {instance: 90.1},
            "ssd_used": {instance: 238 * gib},
            "ssd_total": {instance: 500 * gib},
        },
        instance,
    )

    assert metrics is not None
    assert metrics.cpu.usage_percent == 65.0
    assert metrics.cpu.zone == "green"
    assert metrics.cpu.detail.model_dump() == {"value": None, "total": 8, "unit": "cores"}
    assert metrics.ram.zone == "yellow"
    assert metrics.ssd.zone == "red"
    assert metrics.ram.detail.model_dump() == {"value": 11.5, "total": 16.0, "unit": "GB"}


@pytest.mark.asyncio
async def test_monitoring_prometheus_up_zero_is_offline_without_502() -> None:
    instance = "10.0.0.1:9100"

    class FakePrometheus:
        async def query(self, _promql: str) -> list[dict[str, object]]:
            return [{"metric": {"instance": instance}, "value": [1000, "0"]}]

    result = await MonitoringService(FakePrometheus()).fetch_one(instance)  # type: ignore[arg-type]

    assert result.online is False
    assert result.metrics is None
    assert result.uptime_seconds is None


@pytest.mark.asyncio
async def test_monitoring_fetch_for_two_instances_uses_valid_regex_and_maps_both() -> None:
    inst_a = "37.27.192.211:9100"
    inst_b = "10.0.0.12:9100"
    seen_queries: list[str] = []
    gib = 1024**3

    def vector(instance: str, value: float, timestamp: float = 1000.0) -> dict[str, object]:
        return {"metric": {"instance": instance}, "value": [timestamp, str(value)]}

    class FakePrometheus:
        async def query(self, promql: str) -> list[dict[str, object]]:
            seen_queries.append(promql)
            assert 'instance=~"37\\\\.27\\\\.192\\\\.211:9100|10\\\\.0\\\\.0\\\\.12:9100"' in promql
            assert "\\." not in promql.replace("\\\\.", "")
            if promql.startswith("up{"):
                return [vector(inst_a, 1), vector(inst_b, 1)]
            if "node_time_seconds" in promql:
                return [vector(inst_a, 3600), vector(inst_b, 7200)]
            if "node_cpu_seconds_total" in promql and "count by" not in promql:
                return [vector(inst_a, 65.04), vector(inst_b, 72.25)]
            if "count by" in promql:
                return [vector(inst_a, 8), vector(inst_b, 4)]
            if "node_memory_MemAvailable" in promql and "* 100" in promql:
                return [vector(inst_a, 80), vector(inst_b, 55)]
            if promql.startswith("node_memory_MemTotal_bytes"):
                return [vector(inst_a, 16 * gib), vector(inst_b, 8 * gib)]
            if "node_memory_MemTotal_bytes" in promql and "-" in promql:
                return [vector(inst_a, 11.5 * gib), vector(inst_b, 3 * gib)]
            if "node_filesystem_avail_bytes" in promql and "* 100" in promql:
                return [vector(inst_a, 90.1), vector(inst_b, 40)]
            if promql.startswith("node_filesystem_size_bytes"):
                return [vector(inst_a, 500 * gib), vector(inst_b, 100 * gib)]
            if "node_filesystem_size_bytes" in promql and "-" in promql:
                return [vector(inst_a, 238 * gib), vector(inst_b, 40 * gib)]
            return []

    result = await MonitoringService(FakePrometheus()).fetch_for_instances([inst_a, inst_b])  # type: ignore[arg-type]

    assert len(seen_queries) == 10
    assert set(result) == {inst_a, inst_b}
    assert result[inst_a].online is True
    metrics_a = result[inst_a].metrics
    assert metrics_a is not None
    assert metrics_a.cpu.detail.total == 8
    assert result[inst_b].online is True
    metrics_b = result[inst_b].metrics
    assert metrics_b is not None
    assert metrics_b.cpu.detail.total == 4


@pytest.mark.asyncio
async def test_monitoring_cache_uses_ttl_without_second_prometheus_call() -> None:
    instance = "10.0.0.12:9100"
    calls = 0

    class FakePrometheus:
        async def query(self, _promql: str) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            return [{"metric": {"instance": instance}, "value": [1000, "0"]}]

    service = MonitoringService(FakePrometheus())  # type: ignore[arg-type]

    first = await service.fetch_for_instances([instance])
    second = await service.fetch_for_instances([instance])

    assert first is second
    assert calls == 10


@pytest.mark.asyncio
async def test_monitoring_single_flight_collapses_concurrent_same_key() -> None:
    instance = "10.0.0.12:9100"
    calls = 0
    release = asyncio.Event()

    class FakePrometheus:
        async def query(self, _promql: str) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            await release.wait()
            return [{"metric": {"instance": instance}, "value": [1000, "0"]}]

    service = MonitoringService(FakePrometheus())  # type: ignore[arg-type]
    tasks = [asyncio.create_task(service.fetch_for_instances([instance])) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)

    assert all(result is results[0] for result in results)
    assert calls == 10
