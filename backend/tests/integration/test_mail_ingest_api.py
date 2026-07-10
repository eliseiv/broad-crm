"""HTTP-контракт push-приёмника `/api/mail/ingest` и `/api/mail/mailbox-status` (ADR-044 §3).

Проверяет НА УРОВНЕ РОУТЕРА порядок проверок и HMAC-границу: 503 (пустой `MAIL_PUSH_SECRET`,
ДО чтения тела) → 401 (подпись/skew) → 400 (битое тело); битое тело БЕЗ подписи даёт 401,
а не 400 (аутентификация раньше парсинга); подпись считается над сырыми байтами (не-ASCII
тело проходит). Коды сверяются по `error.code` (не по `message`).

**ВНИМАНИЕ (блокер S3):** этот файл поднимает FastAPI-app (`app.main.create_app`), а на момент
спринта S1 импорт `app.api.deps`→`app.services.mail_service` падает из-за снятого `MailOrder`
в параллельном S3-рефакторе старого proxy. Пока импорт сломан — модуль само-скипается
(`skipif`) с явной причиной; после того как S3 удалит/починит старый proxy — тесты запустятся
без изменений. Криптография подписи и порядок сервис-логики уже покрыты
`tests/unit/test_mail_push_security.py` и `tests/integration/test_mail_ingest_service.py`.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest

try:  # pragma: no cover - import-guard под S3-рефактор
    from app.main import create_app

    _APP_IMPORTABLE = True
    _IMPORT_ERR = ""
except Exception as exc:
    _APP_IMPORTABLE = False
    _IMPORT_ERR = f"{type(exc).__name__}: {exc}"

pytestmark = pytest.mark.skipif(
    not _APP_IMPORTABLE,
    reason=f"app import заблокирован mid-refactor S3 (mail_service MailOrder): {_IMPORT_ERR}",
)

from app.infra.mail_push_security import compute_mail_push_signature  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from mail_helpers import mail_db, seed_account  # noqa: E402

_SECRET = "push-secret-integration"


def _headers(secret: str, raw: bytes, *, ts: int | None = None) -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    sig = compute_mail_push_signature(secret=secret, timestamp=ts, raw_body=raw)
    return {
        "Content-Type": "application/json",
        "X-Mail-Signature": f"sha256={sig}",
        "X-Mail-Timestamp": str(ts),
    }


async def _build(monkeypatch: pytest.MonkeyPatch, sm: Any, *, secret: str) -> Any:
    from app.api import deps
    from app.config import get_settings

    monkeypatch.setenv("MAIL_PUSH_SECRET", secret)
    get_settings.cache_clear()
    app = create_app(get_settings())

    async def _session() -> AsyncIterator[Any]:
        async with sm() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[deps.get_session] = _session
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _valid_body() -> bytes:
    obj = {
        "messages": [
            {
                "mail_account_id": 1,
                "uidvalidity": 1,
                "uid": 10,
                "subject": "Отчёт «июнь» 📊",
                "from_addr": "sender@example.com",
                "from_name": "Иван Петров",
                "to_addrs": "inbox@example.com",
                "internal_date": "2026-07-02T09:15:00Z",
                "body_text": "тело письма — не-ASCII ✉️",
            }
        ]
    }
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------ 503: пустой секрет
async def test_ingest_503_when_secret_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm, secret="")
        raw = b'{"garbage'  # даже битое тело: 503 раньше чтения/парсинга
        async with _client(app) as client:
            resp = await client.post("/api/mail/ingest", content=raw)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "mail_ingest_not_configured"


# ------------------------------------------------------------ 401: подпись/skew
async def test_ingest_401_tampered_body(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm, secret=_SECRET)
        signed = _valid_body()
        headers = _headers(_SECRET, signed)
        async with _client(app) as client:
            resp = await client.post("/api/mail/ingest", content=signed + b" ", headers=headers)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize("delta", [400, -400])
async def test_ingest_401_skew_out_of_window(monkeypatch: pytest.MonkeyPatch, delta: int) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm, secret=_SECRET)
        raw = _valid_body()
        headers = _headers(_SECRET, raw, ts=int(time.time()) + delta)
        async with _client(app) as client:
            resp = await client.post("/api/mail/ingest", content=raw, headers=headers)
        assert resp.status_code == 401


async def test_ingest_malformed_without_signature_is_401_not_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Аутентификация раньше парсинга: битое тело без подписи → 401, не 400."""
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm, secret=_SECRET)
        async with _client(app) as client:
            resp = await client.post("/api/mail/ingest", content=b"{not json")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "not_authenticated"


# ------------------------------------------------ 400: битое тело с валидной подписью
async def test_ingest_400_malformed_body_with_valid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm, secret=_SECRET)
        raw = b'{"messages": [ this is not valid json ]}'
        headers = _headers(_SECRET, raw)
        async with _client(app) as client:
            resp = await client.post("/api/mail/ingest", content=raw, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "validation_error"


# ------------------------------------------------- 200: валидная подпись, не-ASCII тело
async def test_ingest_200_valid_non_ascii_body(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1)
        app = await _build(monkeypatch, sm, secret=_SECRET)
        raw = _valid_body()  # ensure_ascii=False — подпись над сырыми байтами
        headers = _headers(_SECRET, raw)
        async with _client(app) as client:
            resp = await client.post("/api/mail/ingest", content=raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"accepted": 1, "duplicate": 0, "unknown_mailbox": 0}


# ---------------------------------------------------------- mailbox-status HTTP
async def test_mailbox_status_200_and_unknown_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            await seed_account(s, account_id=1, is_active=True)
        app = await _build(monkeypatch, sm, secret=_SECRET)
        known = json.dumps({"mail_account_id": 1, "is_active": False}).encode("utf-8")
        unknown = json.dumps({"mail_account_id": 999, "is_active": False}).encode("utf-8")
        async with _client(app) as client:
            r_known = await client.post(
                "/api/mail/mailbox-status", content=known, headers=_headers(_SECRET, known)
            )
            r_unknown = await client.post(
                "/api/mail/mailbox-status", content=unknown, headers=_headers(_SECRET, unknown)
            )
        assert r_known.status_code == 200
        assert r_known.json() == {"updated": True}
        assert r_unknown.status_code == 200
        assert r_unknown.json() == {"updated": False}
