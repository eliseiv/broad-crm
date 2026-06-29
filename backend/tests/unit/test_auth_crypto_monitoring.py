from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.config import get_settings
from app.domain.thresholds import usage_to_zone
from app.errors import AppError
from app.infra.crypto import CryptoError, decrypt_password, encrypt_password
from app.infra.jwt import TokenError, decode_access_token, issue_access_token
from app.infra.rate_limit import InMemoryRateLimiter
from app.services import auth_service
from app.services.auth_service import AuthService
from app.services.monitoring_service import MonitoringService, _build_metrics, _cpu_detail


def test_auth_valid_and_invalid_credentials_expected_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    service = AuthService(
        settings=settings,
        rate_limiter=InMemoryRateLimiter(max_attempts=10, window_sec=300),
    )

    token = service.login(username="admin", password="secret", client_ip="10.0.0.1")

    assert token.token_type == "bearer"
    assert decode_access_token(token.access_token) == "admin"

    for username, password in [("admin", "bad"), ("missing", "secret")]:
        with pytest.raises(AppError) as exc:
            service.login(username=username, password=password, client_ip="10.0.0.2")
        assert exc.value.status_code == 401
        assert exc.value.code == "invalid_credentials"
        assert exc.value.message == "Неверный логин или пароль"

    calls: list[tuple[str, str]] = []

    def spy_compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(auth_service.secrets, "compare_digest", spy_compare)
    with pytest.raises(AppError):
        service.login(username="missing", password="bad", client_ip="10.0.0.3")

    assert calls == [("missing", "admin"), ("bad", "secret")]


def test_auth_rate_limit_returns_429() -> None:
    service = AuthService(
        settings=get_settings(),
        rate_limiter=InMemoryRateLimiter(max_attempts=2, window_sec=300),
    )

    for _ in range(2):
        with pytest.raises(AppError):
            service.login(username="admin", password="bad", client_ip="10.0.0.9")

    with pytest.raises(AppError) as exc:
        service.login(username="admin", password="secret", client_ip="10.0.0.9")

    assert exc.value.status_code == 429
    assert exc.value.code == "rate_limited"


def test_jwt_missing_invalid_and_expired_tokens_are_rejected() -> None:
    token, expires_in = issue_access_token("admin")

    assert expires_in == 3600
    assert decode_access_token(token) == "admin"

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
