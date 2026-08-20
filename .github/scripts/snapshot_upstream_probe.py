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
                    LIMIT 5
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
        paths = (
            # ВАЖЕН порядок: `/products` — это probe детекции префикса (ADR-072 §4а).
            # Если он падает, клиент не определяет префикс и вся операция валится,
            # даже когда `/users` полностью исправен.
            "/products",
            "/users?limit=1&offset=0",
            "/users?limit=100&offset=0",
            "/stats",
        )
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            for prefix, path in [(p, q) for p in _PREFIXES for q in paths]:
                url = f"{domain.rstrip('/')}{prefix}{path}"
                try:
                    resp = await client.get(url, headers={"X-Admin-Key": key})
                except Exception as exc:  # noqa: BLE001 — важен любой транспортный отказ
                    print(f"    {prefix}{path} -> TRANSPORT {type(exc).__name__}: {exc}")
                    continue
                body = resp.text[:_BODY_LIMIT].replace("\n", " ")
                print(f"    {prefix}{path} -> HTTP {resp.status_code}  body={body[:160]!r}")
                # Заголовки, объясняющие 429/5xx на стороне апстрима.
                for header in ("retry-after", "x-ratelimit-remaining", "server"):
                    if header in resp.headers:
                        print(f"        {header}: {resp.headers[header]}")
        # Карточка пользователя — единственный вызов цикла, не покрытый выше.
        # Именно она нужна фазе экономики (`revenue.providers`), и её отказ после
        # хотфикса роняет ВЕСЬ цикл бэка (транспортные ошибки перестали глотаться).
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            try:
                lst = await client.get(
                    f"{domain.rstrip('/')}/v1/admin/users?limit=1&offset=0",
                    headers={"X-Admin-Key": key},
                )
                items = lst.json().get("items") or lst.json().get("users") or []
                if items:
                    uid = items[0].get("id")
                    card = await client.get(
                        f"{domain.rstrip('/')}/v1/admin/users/{uid}",
                        headers={"X-Admin-Key": key},
                    )
                    body = card.text[:200].replace("\n", " ")
                    print(f"    КАРТОЧКА /v1/admin/users/{uid} -> HTTP {card.status_code} {body!r}")
                else:
                    print("    КАРТОЧКА: список пуст, проверять нечего")
            except Exception as exc:  # noqa: BLE001
                print(f"    КАРТОЧКА -> TRANSPORT {type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
