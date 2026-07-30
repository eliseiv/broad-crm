"""Синхронизация оценочного баланса AI-ключей через Admin Cost API (ADR-070).

Admin API key НИКОГДА не логируется. Inference-ключ здесь не используется.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

import httpx

from app.config import get_settings
from app.logging import get_logger
from app.models.ai_key import AiProvider

logger = get_logger(__name__)

SyncOutcome = Literal["ok", "error", "unknown"]

_BACKOFF_DELAYS_SEC = (0.2, 0.5)
_DEFAULT_THRESHOLD_USD = Decimal("10.0000")

REASON_ADMIN_INVALID = "Admin API key недействителен"
REASON_ADMIN_FORBIDDEN = "Admin API key: доступ запрещён"
REASON_KEY_NOT_FOUND = "Ключ не найден в организации провайдера"
REASON_PROVIDER = "Ошибка billing API провайдера"
REASON_NO_ANCHOR = "Якорь баланса не задан"


@dataclass(frozen=True)
class BalanceSyncResult:
    """Результат sync: исход + данные при успехе / причина при error."""

    outcome: SyncOutcome
    spent_usd: Decimal | None = None
    remaining_usd: Decimal | None = None
    provider_api_key_id: str | None = None
    reason: str | None = None


def default_low_threshold_usd() -> Decimal:
    return _DEFAULT_THRESHOLD_USD


def compute_alert_level(remaining_usd: Decimal, threshold_usd: Decimal) -> str:
    """Уровень алерта по остатку (normal / low / depleted)."""
    if remaining_usd <= Decimal("0"):
        return "depleted"
    if remaining_usd <= threshold_usd:
        return "low"
    return "normal"


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    if isinstance(value, dict):
        nested = value.get("value")
        if nested is not None:
            return _parse_decimal(nested)
    return None


def _mask_tail(value: str | None) -> str | None:
    if not value:
        return None
    return value[-4:] if len(value) >= 4 else value


def _matches_key_fragments(
    *,
    prefix: str | None,
    last4: str | None,
    candidate_prefix: str | None,
    candidate_last4: str | None,
    candidate_name: str | None,
) -> bool:
    """Матч inference-ключа по prefix+last4 в списке Admin API keys."""
    if last4 and candidate_last4 and last4 == candidate_last4:
        if prefix and candidate_prefix:
            return prefix == candidate_prefix
        return True
    return bool(prefix and candidate_name and prefix in candidate_name)


async def _request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    max_attempts = len(_BACKOFF_DELAYS_SEC) + 1
    for attempt in range(max_attempts):
        try:
            response = await client.request(method, url, headers=headers, params=params)
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt < max_attempts - 1:
                await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
                continue
            raise
        if 500 <= response.status_code < 600 and attempt < max_attempts - 1:
            await asyncio.sleep(_BACKOFF_DELAYS_SEC[attempt])
            continue
        return response
    raise RuntimeError("retry loop exhausted without response")


def _map_admin_auth_error(status_code: int) -> BalanceSyncResult | None:
    if status_code == httpx.codes.UNAUTHORIZED:
        return BalanceSyncResult("error", reason=REASON_ADMIN_INVALID)
    if status_code == httpx.codes.FORBIDDEN:
        return BalanceSyncResult("error", reason=REASON_ADMIN_FORBIDDEN)
    if status_code >= 400:
        return BalanceSyncResult("error", reason=REASON_PROVIDER)
    return None


async def _find_openai_api_key_id(
    client: httpx.AsyncClient,
    admin_key: str,
    key_prefix: str | None,
    key_last4: str | None,
) -> BalanceSyncResult | str:
    settings = get_settings()
    url = f"{settings.openai_api_base.rstrip('/')}/organization/api_keys"
    headers = {"Authorization": f"Bearer {admin_key}"}
    response = await _request_with_retries(client, "GET", url, headers=headers)
    auth_err = _map_admin_auth_error(response.status_code)
    if auth_err is not None:
        return auth_err
    if response.status_code != httpx.codes.OK:
        return BalanceSyncResult("error", reason=REASON_PROVIDER)
    payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return BalanceSyncResult("error", reason=REASON_PROVIDER)
    for item in items:
        if not isinstance(item, dict):
            continue
        api_key_id = item.get("id")
        if not isinstance(api_key_id, str):
            continue
        name = item.get("name") if isinstance(item.get("name"), str) else None
        raw_redacted = item.get("redacted_value")
        redacted = raw_redacted if isinstance(raw_redacted, str) else None
        cand_last4 = _mask_tail(redacted)
        cand_prefix = redacted[:4] if redacted and len(redacted) >= 4 else None
        if _matches_key_fragments(
            prefix=key_prefix,
            last4=key_last4,
            candidate_prefix=cand_prefix,
            candidate_last4=cand_last4,
            candidate_name=name,
        ):
            return api_key_id
    return BalanceSyncResult("error", reason=REASON_KEY_NOT_FOUND)


async def _find_anthropic_api_key_id(
    client: httpx.AsyncClient,
    admin_key: str,
    key_prefix: str | None,
    key_last4: str | None,
) -> BalanceSyncResult | str:
    settings = get_settings()
    url = f"{settings.anthropic_api_base.rstrip('/')}/organizations/api_keys"
    headers = {
        "x-api-key": admin_key,
        "anthropic-version": settings.anthropic_api_version,
    }
    response = await _request_with_retries(client, "GET", url, headers=headers)
    auth_err = _map_admin_auth_error(response.status_code)
    if auth_err is not None:
        return auth_err
    if response.status_code != httpx.codes.OK:
        return BalanceSyncResult("error", reason=REASON_PROVIDER)
    payload = response.json()
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return BalanceSyncResult("error", reason=REASON_PROVIDER)
    for item in items:
        if not isinstance(item, dict):
            continue
        api_key_id = item.get("id")
        if not isinstance(api_key_id, str):
            continue
        name = item.get("name") if isinstance(item.get("name"), str) else None
        raw_partial = item.get("partial_key_hint")
        partial = raw_partial if isinstance(raw_partial, str) else None
        cand_last4 = _mask_tail(partial)
        cand_prefix = partial[:4] if partial and len(partial) >= 4 else None
        if _matches_key_fragments(
            prefix=key_prefix,
            last4=key_last4,
            candidate_prefix=cand_prefix,
            candidate_last4=cand_last4,
            candidate_name=name,
        ):
            return api_key_id
    return BalanceSyncResult("error", reason=REASON_KEY_NOT_FOUND)


async def _sum_openai_costs(
    client: httpx.AsyncClient,
    admin_key: str,
    *,
    anchor_at: datetime,
    api_key_id: str,
) -> BalanceSyncResult | Decimal:
    settings = get_settings()
    base = settings.openai_api_base.rstrip("/")
    url = f"{base}/organization/costs"
    headers = {"Authorization": f"Bearer {admin_key}"}
    start_time = int(anchor_at.timestamp())
    params: dict[str, Any] = {
        "start_time": start_time,
        "bucket_width": "1d",
        "group_by": ["api_key_id"],
        "limit": 180,
    }
    total = Decimal("0")
    page: str | None = None
    while True:
        req_params = dict(params)
        if page:
            req_params["page"] = page
        response = await _request_with_retries(
            client, "GET", url, headers=headers, params=req_params
        )
        auth_err = _map_admin_auth_error(response.status_code)
        if auth_err is not None:
            return auth_err
        if response.status_code != httpx.codes.OK:
            return BalanceSyncResult("error", reason=REASON_PROVIDER)
        payload = response.json()
        if not isinstance(payload, dict):
            return BalanceSyncResult("error", reason=REASON_PROVIDER)
        buckets = payload.get("data")
        if isinstance(buckets, list):
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                results = bucket.get("results")
                if not isinstance(results, list):
                    continue
                for row in results:
                    if not isinstance(row, dict):
                        continue
                    row_key_id = row.get("api_key_id")
                    if row_key_id is not None and row_key_id != api_key_id:
                        continue
                    amount = _parse_decimal(row.get("amount"))
                    if amount is not None:
                        total += amount
        if not payload.get("has_more"):
            break
        next_page = payload.get("next_page")
        if not isinstance(next_page, str) or not next_page:
            break
        page = next_page
    return total


def _chunk_utc_days(
    start: datetime, end: datetime, max_days: int = 31
) -> list[tuple[datetime, datetime]]:
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=max_days), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


async def _sum_anthropic_costs(
    client: httpx.AsyncClient,
    admin_key: str,
    *,
    anchor_at: datetime,
    api_key_id: str,
) -> BalanceSyncResult | Decimal:
    settings = get_settings()
    base = settings.anthropic_api_base.rstrip("/")
    url = f"{base}/organizations/cost_report"
    headers = {
        "x-api-key": admin_key,
        "anthropic-version": settings.anthropic_api_version,
    }
    end = datetime.now(UTC)
    total_cents = Decimal("0")
    for chunk_start, chunk_end in _chunk_utc_days(anchor_at, end):
        params: dict[str, Any] = {
            "starting_at": chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ending_at": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bucket_width": "1d",
            "limit": 31,
            "api_key_ids": [api_key_id],
        }
        page: str | None = None
        while True:
            req_params = dict(params)
            if page:
                req_params["page"] = page
            response = await _request_with_retries(
                client, "GET", url, headers=headers, params=req_params
            )
            auth_err = _map_admin_auth_error(response.status_code)
            if auth_err is not None:
                return auth_err
            if response.status_code != httpx.codes.OK:
                return BalanceSyncResult("error", reason=REASON_PROVIDER)
            payload = response.json()
            if not isinstance(payload, dict):
                return BalanceSyncResult("error", reason=REASON_PROVIDER)
            buckets = payload.get("data")
            if isinstance(buckets, list):
                for bucket in buckets:
                    if not isinstance(bucket, dict):
                        continue
                    results = bucket.get("results")
                    if not isinstance(results, list):
                        continue
                    for row in results:
                        if not isinstance(row, dict):
                            continue
                        row_key_id = row.get("api_key_id")
                        if row_key_id is not None and row_key_id != api_key_id:
                            continue
                        cents = _parse_decimal(row.get("amount"))
                        if cents is not None:
                            total_cents += cents
            if not payload.get("has_more"):
                break
            next_page = payload.get("next_page")
            if not isinstance(next_page, str) or not next_page:
                break
            page = next_page
    return total_cents / Decimal("100")


async def sync_balance(
    provider: AiProvider,
    admin_key: str,
    *,
    key_prefix: str | None,
    key_last4: str | None,
    balance_initial_usd: Decimal,
    balance_anchor_at: datetime,
    cached_api_key_id: str | None = None,
) -> BalanceSyncResult:
    """Синхронизирует spent с якоря и вычисляет остаток."""
    if balance_anchor_at.tzinfo is None:
        balance_anchor_at = balance_anchor_at.replace(tzinfo=UTC)

    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.ai_provider_timeout_sec, verify=True) as client:
        api_key_id = cached_api_key_id
        if api_key_id is None:
            found = (
                await _find_openai_api_key_id(client, admin_key, key_prefix, key_last4)
                if provider is AiProvider.openai
                else await _find_anthropic_api_key_id(client, admin_key, key_prefix, key_last4)
            )
            if isinstance(found, BalanceSyncResult):
                return found
            api_key_id = found

        spent = (
            await _sum_openai_costs(
                client, admin_key, anchor_at=balance_anchor_at, api_key_id=api_key_id
            )
            if provider is AiProvider.openai
            else await _sum_anthropic_costs(
                client, admin_key, anchor_at=balance_anchor_at, api_key_id=api_key_id
            )
        )
        if isinstance(spent, BalanceSyncResult):
            return spent

        remaining = balance_initial_usd - spent
        return BalanceSyncResult(
            outcome="ok",
            spent_usd=spent,
            remaining_usd=remaining,
            provider_api_key_id=api_key_id,
        )


__all__ = [
    "BalanceSyncResult",
    "REASON_ADMIN_FORBIDDEN",
    "REASON_ADMIN_INVALID",
    "REASON_KEY_NOT_FOUND",
    "REASON_NO_ANCHOR",
    "REASON_PROVIDER",
    "compute_alert_level",
    "default_low_threshold_usd",
    "sync_balance",
]
