"""Unit-тесты Admin Cost API адаптера (mock httpx)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from app.infra import ai_provider_billing as mod
from app.infra.ai_provider_billing import sync_balance
from app.models.ai_key import AiProvider

ADMIN_KEY = "sk-admin-TEST-KEY-12345678"
INFERENCE_PREFIX = "sk-p"
INFERENCE_LAST4 = "bA3T"


def _install(monkeypatch: pytest.MonkeyPatch, handler: httpx.MockTransport) -> None:
    real_async_client = httpx.AsyncClient

    def factory(*_args: object, **_kwargs: object) -> httpx.AsyncClient:
        return real_async_client(transport=handler)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)


async def test_openai_sync_sums_costs_for_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/organization/api_keys"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "key_abc",
                            "name": "prod",
                            "redacted_value": f"{INFERENCE_PREFIX}…{INFERENCE_LAST4}",
                        }
                    ]
                },
            )
        if request.url.path.endswith("/organization/costs"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "results": [
                                {
                                    "api_key_id": "key_abc",
                                    "amount": {"value": "1.50"},
                                },
                                {
                                    "api_key_id": "key_other",
                                    "amount": {"value": "9.00"},
                                },
                            ]
                        }
                    ],
                    "has_more": False,
                },
            )
        return httpx.Response(404)

    _install(monkeypatch, httpx.MockTransport(handler))
    anchor = datetime(2026, 1, 1, tzinfo=UTC)
    result = await sync_balance(
        AiProvider.openai,
        ADMIN_KEY,
        key_prefix=INFERENCE_PREFIX,
        key_last4=INFERENCE_LAST4,
        balance_initial_usd=Decimal("100"),
        balance_anchor_at=anchor,
    )
    assert result.outcome == "ok"
    assert result.spent_usd == Decimal("1.50")
    assert result.remaining_usd == Decimal("98.50")
    assert result.provider_api_key_id == "key_abc"
    assert "/organization/api_keys" in calls[0]
    assert "/organization/costs" in calls[1]
