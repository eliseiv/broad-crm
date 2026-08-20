"""One-off probe (READ-ONLY): что внешний контур CRM отвечает боту про пользователя.

Запускается ВНУТРИ backend-контейнера (`docker compose exec -T backend python - <tg_id> <nick>`),
дёргает `GET /api/external/documents/user-access/{telegram_user_id}` — тот же резолв
(knowledge → sms → mail → bootstrap по username), что и `POST /knowledge-bot/link`,
но БЕЗ записи линка. Ничего не изменяет. Удалить после разбора инцидента.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/external/documents"


def probe(label: str, url: str, key: str) -> None:
    req = urllib.request.Request(url, headers={"X-API-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"{label}: HTTP {resp.status}\n  {resp.read().decode()}")
    except urllib.error.HTTPError as exc:
        print(f"{label}: HTTP {exc.code}\n  {exc.read().decode()}")
    except Exception as exc:  # noqa: BLE001 — диагностика, важен любой отказ
        print(f"{label}: ERROR {type(exc).__name__}: {exc}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python - <telegram_user_id> [username]")
        return 2
    tg = sys.argv[1]
    nick = sys.argv[2] if len(sys.argv) > 2 else ""

    key = os.environ.get("DOCUMENTS_API_KEY", "")
    if not key:
        print("DOCUMENTS_API_KEY пуст в контейнере — внешний контур выключен (503)")
        return 0

    probe("resolve С username", f"{BASE}/user-access/{tg}?username={nick}", key)
    probe("resolve БЕЗ username", f"{BASE}/user-access/{tg}", key)
    probe("дерево документов", f"{BASE}/tree?telegram_user_id={tg}", key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
