"""Сумма provider_cost_usd за последние 7 дней по бэкам Claude IOS / Claude РФ.

Запуск внутри контейнера backend: python /app/claude_request_cost_report.py
Пишет прогресс в stdout; чекпоинт — /tmp/claude_cost_checkpoint.json
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import get_sessionmaker
from app.errors import AppError
from app.infra.backend_admin_client import BackendAdminClient
from app.infra.crypto import decrypt_secret
from app.models.service_backend import Backend

CUTOFF = datetime.now(timezone.utc) - timedelta(days=7)
SEM = asyncio.Semaphore(2)
PAGE = 100
TARGET_NAMES = {"Claude IOS", "Claude РФ"}
CHECKPOINT = Path("/tmp/claude_cost_checkpoint.json")
SLEEP_BETWEEN_BATCHES = 0.35
SLEEP_ON_429 = 8.0
MAX_429_RETRIES = 8


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        v = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(v)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def cost_of(item):
    if item.get("refunded") is True:
        return None
    c = item.get("provider_cost_usd")
    if c is None:
        return None
    try:
        return float(c)
    except (TypeError, ValueError):
        return None


def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            return json.loads(CHECKPOINT.read_text())
        except Exception:
            return {}
    return {}


def save_checkpoint(data: dict) -> None:
    CHECKPOINT.write_text(json.dumps(data, ensure_ascii=False, indent=2))


async def call_with_retry(fn, *args, **kwargs):
    last = None
    for attempt in range(MAX_429_RETRIES):
        try:
            return await fn(*args, **kwargs)
        except AppError as e:
            last = e
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                wait = SLEEP_ON_429 * (attempt + 1)
                print(f"RETRY_429 wait={wait}s attempt={attempt+1}", flush=True)
                await asyncio.sleep(wait)
                continue
            raise
        except Exception as e:
            last = e
            await asyncio.sleep(2.0 * (attempt + 1))
    raise last  # type: ignore[misc]


async def sum_user(client: BackendAdminClient, user_id: str) -> tuple[float, int]:
    total = 0.0
    counted = 0
    offset = 0
    pages = 0
    while pages < 40:
        async with SEM:
            try:
                payload = await call_with_retry(
                    client.list_requests, user_id, limit=PAGE, offset=offset
                )
            except Exception:
                break
        pages += 1
        items = payload.get("items") or []
        if not items:
            break
        stop = False
        for it in items:
            sent = parse_dt(it.get("sent_at"))
            if sent is not None and sent < CUTOFF:
                stop = True
                break
            if sent is None:
                continue
            c = cost_of(it)
            if c is not None:
                total += c
                counted += 1
        if stop or len(items) < PAGE:
            break
        offset += PAGE
    return total, counted


async def process_backend(row: Backend, ck: dict) -> dict:
    code = row.code
    name = row.name
    state = ck.get("backends", {}).setdefault(
        code,
        {
            "code": code,
            "name": name,
            "offset": 0,
            "users_listed": 0,
            "users_scanned": 0,
            "requests_counted": 0,
            "sum_usd": 0.0,
            "errors": 0,
            "status": "pending",
        },
    )
    if state.get("status") == "ok":
        print(f"RESUME_SKIP done code={code!r}", flush=True)
        return state

    print(
        f"\nSTART code={code!r} name={name!r} domain={row.domain} "
        f"resume_offset={state.get('offset', 0)} sum_so_far={state.get('sum_usd', 0):.6f}",
        flush=True,
    )
    if not row.admin_api_key_encrypted:
        state["status"] = "skip_no_admin_key"
        save_checkpoint(ck)
        return state
    try:
        key = decrypt_secret(row.admin_api_key_encrypted)
    except Exception as e:
        state["status"] = "decrypt_fail"
        state["error"] = str(e)
        save_checkpoint(ck)
        return state

    client = BackendAdminClient(row.id, row.domain, key)
    lock = asyncio.Lock()
    offset = int(state.get("offset") or 0)

    while True:
        try:
            listing = await call_with_retry(client.list_users, limit=PAGE, offset=offset)
        except Exception as e:
            state["status"] = f"list_fail:{type(e).__name__}"
            state["errors"] = int(state.get("errors") or 0) + 1
            state["error"] = str(e)
            print(f"FAIL list {code}: {type(e).__name__}: {e}", flush=True)
            save_checkpoint(ck)
            break

        items = listing.get("items") or []
        if not items:
            state["status"] = "ok"
            save_checkpoint(ck)
            break

        state["users_listed"] = int(state.get("users_listed") or 0) + len(items)

        async def one(u: dict):
            uid = str(u.get("id") or "")
            if not uid:
                return
            try:
                s, n = await sum_user(client, uid)
            except Exception:
                async with lock:
                    state["errors"] = int(state.get("errors") or 0) + 1
                return
            async with lock:
                state["users_scanned"] = int(state.get("users_scanned") or 0) + 1
                state["sum_usd"] = float(state.get("sum_usd") or 0) + s
                state["requests_counted"] = int(state.get("requests_counted") or 0) + n

        for i in range(0, len(items), 10):
            await asyncio.gather(*(one(u) for u in items[i : i + 10]))
            await asyncio.sleep(SLEEP_BETWEEN_BATCHES)

        offset += PAGE
        state["offset"] = offset
        save_checkpoint(ck)
        print(
            f"progress {code}: listed={state['users_listed']} scanned={state['users_scanned']} "
            f"sum_usd={float(state['sum_usd']):.6f} errors={state['errors']} next_offset={offset}",
            flush=True,
        )
        if len(items) < PAGE:
            state["status"] = "ok"
            save_checkpoint(ck)
            break

    print(
        f"DONE {code}: sum_usd={float(state['sum_usd']):.6f} "
        f"requests={state['requests_counted']} listed={state['users_listed']} "
        f"status={state['status']}",
        flush=True,
    )
    await asyncio.sleep(1.0)
    return state


async def main():
    print(f"CUTOFF_UTC={CUTOFF.isoformat()} PERIOD=last_7_days SEM=2", flush=True)
    ck = load_checkpoint()
    ck.setdefault("started_at", datetime.now(timezone.utc).isoformat())
    ck.setdefault("backends", {})
    save_checkpoint(ck)

    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(Backend)
                .where(Backend.name.in_(tuple(TARGET_NAMES)))
                .order_by(Backend.name, Backend.code)
            )
        ).scalars().all()

    print(f"TARGET_BACKENDS={len(rows)}", flush=True)
    for r in rows:
        print(
            f"  - {r.code} | {r.name} | admin={bool(r.admin_api_key_encrypted)} | {r.domain}",
            flush=True,
        )

    results = []
    for r in rows:
        results.append(await process_backend(r, ck))

    print("\n=== PER BACKEND ===", flush=True)
    by_name = {"Claude IOS": 0.0, "Claude РФ": 0.0}
    by_req = {"Claude IOS": 0, "Claude РФ": 0}
    for r in results:
        print(
            f"{r['name']}|{r['code']}|sum_usd={float(r.get('sum_usd') or 0):.6f}|"
            f"requests={r.get('requests_counted')}|users_listed={r.get('users_listed')}|"
            f"status={r.get('status')}|errors={r.get('errors')}",
            flush=True,
        )
        if r.get("name") in by_name:
            by_name[r["name"]] += float(r.get("sum_usd") or 0)
            by_req[r["name"]] += int(r.get("requests_counted") or 0)

    print("\n=== TOTALS BY NAME (включая частичные прогоны) ===", flush=True)
    for name in ("Claude IOS", "Claude РФ"):
        print(f"{name}: sum_usd={by_name[name]:.6f} requests={by_req[name]}", flush=True)
    print(f"GRAND_TOTAL_usd={sum(by_name.values()):.6f}", flush=True)
    ck["finished_at"] = datetime.now(timezone.utc).isoformat()
    ck["totals"] = {"by_name": by_name, "by_req": by_req, "grand": sum(by_name.values())}
    save_checkpoint(ck)
    print("REPORT_OK", flush=True)


if __name__ == "__main__":
    t0 = time.time()
    asyncio.run(main())
    print(f"ELAPSED_SEC={time.time() - t0:.1f}", flush=True)
