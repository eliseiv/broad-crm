"""One-off probe (READ-ONLY): что РЕАЛЬНО отвечает бэк, у которого цикл снимка падает.

Запускается внутри backend-контейнера (`docker compose exec -T backend python -`).
Берёт бэки с admin-ключом, у которых `refreshed_at IS NULL` (снимок так и не собран),
и делает по ОДНОМУ сырому запросу `GET {prefix}/users?limit=1&offset=0`, печатая статус
и НАЧАЛО тела ответа. Тело апстрима — единственный способ отличить «бэк сам падает» от
«мы шлём не то»; по симптому «HTTP 500» это неразличимо.

Секреты не печатаются: admin-ключ не выводится, тело обрезается.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
from sqlalchemy import text

from app.db import get_sessionmaker
from app.infra.crypto import decrypt_secret

_PREFIXES = ("/api/billing/admin", "/v1/admin")
_BODY_LIMIT = 300


async def main() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT b.name, b.code, b.domain, b.admin_api_key_encrypted,
                           s.refreshed_at, left(coalesce(s.error_message, ''), 80) AS err
                    FROM backends b
                    LEFT JOIN backend_user_snapshot_sources s ON s.backend_id = b.id
                    WHERE b.admin_api_key_encrypted IS NOT NULL
                      AND (s.refreshed_at IS NULL)
                    ORDER BY b.name
                    LIMIT 8
                    """
                )
            )
        ).fetchall()

    if not rows:
        print("Все источники с ключом имеют refreshed_at — падающих нет.")
        return

    print(f"Источников без собранного снимка: {len(rows)}\n")
    for name, code, domain, encrypted, refreshed_at, err in rows:
        print(f"--- {name} ({code}) domain={domain} refreshed_at={refreshed_at} err={err!r}")
        key = decrypt_secret(encrypted)
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            for prefix in _PREFIXES:
                url = f"{domain.rstrip('/')}{prefix}/users?limit=1&offset=0"
                try:
                    resp = await client.get(url, headers={"X-Admin-Key": key})
                except Exception as exc:  # noqa: BLE001 — важен любой транспортный отказ
                    print(f"    {prefix}/users -> TRANSPORT {type(exc).__name__}: {exc}")
                    continue
                body = resp.text[:_BODY_LIMIT].replace("\n", " ")
                print(f"    {prefix}/users -> HTTP {resp.status_code}  body={body!r}")
                # Заголовки, объясняющие 429/5xx на стороне апстрима.
                for header in ("retry-after", "x-ratelimit-remaining", "server"):
                    if header in resp.headers:
                        print(f"        {header}: {resp.headers[header]}")
        print()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
