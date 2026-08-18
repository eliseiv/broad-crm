
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db import get_sessionmaker
from app.infra.backend_admin_client import BackendAdminClient
from app.infra.crypto import decrypt_secret
from app.models.service_backend import Backend

CUTOFF = datetime.now(timezone.utc) - timedelta(days=7)
SEM = asyncio.Semaphore(4)
PAGE = 100
TARGET_NAMES = {"Claude IOS", "Claude РФ"}

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

async def sum_user(client, user_id):
    total = 0.0
    counted = 0
    offset = 0
    pages = 0
    while pages < 40:
        async with SEM:
            try:
                payload = await client.list_requests(user_id, limit=PAGE, offset=offset)
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

async def process_backend(row):
    code = row.code
    name = row.name
    print(f"\nSTART code={code!r} name={name!r} domain={row.domain}", flush=True)
    out = {
        "code": code,
        "name": name,
        "users_listed": 0,
        "users_scanned": 0,
        "requests_counted": 0,
        "sum_usd": 0.0,
        "errors": 0,
        "status": "ok",
    }
    if not row.admin_api_key_encrypted:
        out["status"] = "skip_no_admin_key"
        print(f"SKIP {code}: no admin key", flush=True)
        return out
    try:
        key = decrypt_secret(row.admin_api_key_encrypted)
    except Exception as e:
        out["status"] = "decrypt_fail"
        print(f"SKIP {code}: decrypt {e}", flush=True)
        return out

    client = BackendAdminClient(row.id, row.domain, key)
    lock = asyncio.Lock()
    offset = 0
    while True:
        try:
            listing = await client.list_users(limit=PAGE, offset=offset)
        except Exception as e:
            out["status"] = f"list_fail:{type(e).__name__}"
            out["errors"] += 1
            print(f"FAIL list {code}: {type(e).__name__}: {e}", flush=True)
            break
        items = listing.get("items") or []
        if not items:
            break
        out["users_listed"] += len(items)

        async def one(u):
            uid = str(u.get("id") or "")
            if not uid:
                return
            try:
                s, n = await sum_user(client, uid)
            except Exception:
                async with lock:
                    out["errors"] += 1
                return
            async with lock:
                out["users_scanned"] += 1
                out["sum_usd"] += s
                out["requests_counted"] += n

        batch = 20
        for i in range(0, len(items), batch):
            await asyncio.gather(*(one(u) for u in items[i : i + batch]))
            await asyncio.sleep(0.15)

        print(
            f"progress {code}: listed={out['users_listed']} "
            f"scanned={out['users_scanned']} sum_usd={out['sum_usd']:.6f} "
            f"errors={out['errors']}",
            flush=True,
        )
        if len(items) < PAGE:
            break
        offset += PAGE

    print(
        f"DONE {code}: sum_usd={out['sum_usd']:.6f} requests={out['requests_counted']} "
        f"listed={out['users_listed']} status={out['status']}",
        flush=True,
    )
    await asyncio.sleep(0.5)
    return out

async def main():
    print(f"CUTOFF_UTC={CUTOFF.isoformat()} PERIOD=last_7_days SEM=4", flush=True)
    sm = get_sessionmaker()
    async with sm() as db:
        rows = (
            await db.execute(
                select(Backend).where(Backend.name.in_(tuple(TARGET_NAMES))).order_by(Backend.name, Backend.code)
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
        results.append(await process_backend(r))

    print("\n=== PER BACKEND ===", flush=True)
    by_name = {"Claude IOS": 0.0, "Claude РФ": 0.0}
    by_req = {"Claude IOS": 0, "Claude РФ": 0}
    for r in results:
        print(
            f"{r['name']}|{r['code']}|sum_usd={r['sum_usd']:.6f}|"
            f"requests={r['requests_counted']}|users_listed={r['users_listed']}|"
            f"status={r['status']}|errors={r['errors']}",
            flush=True,
        )
        if r["name"] in by_name:
            by_name[r["name"]] += r["sum_usd"]
            by_req[r["name"]] += r["requests_counted"]

    print("\n=== TOTALS BY NAME (включая частичные прогоны) ===", flush=True)
    for name in ("Claude IOS", "Claude РФ"):
        print(f"{name}: sum_usd={by_name[name]:.6f} requests={by_req[name]}", flush=True)
    print(f"GRAND_TOTAL_usd={sum(by_name.values()):.6f}", flush=True)
    print("REPORT_OK", flush=True)

asyncio.run(main())
