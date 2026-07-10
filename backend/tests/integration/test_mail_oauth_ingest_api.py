"""Integration (ADR-045 §3): `POST /api/mail/oauth/ingest` — HMAC + crm_state + upsert.

Реальный Postgres + FastAPI-app. Двойная граница безопасности: внешний HMAC push-контракта
(как `/ingest`: пустой секрет → 503; подпись/skew → 401; битое тело → 400) И подписанный
`crm_state` (битый HMAC/формат → 401; протух → 410 oauth_state_expired). Успех → 200
`{ok:true}` + upsert каталожной записи (`team_id` из crm_state, НЕ из тела). Идемпотентность
и re-consent (без дубля, `team_id` детерминированно перезаписан). Безопасность: `crm_state`
и сырое тело не логируются; спуф `team_id` в теле игнорируется. Коды — по `error.code`.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import structlog
from app.infra.mail_oauth_state import encode_crm_state
from app.infra.mail_push_security import compute_mail_push_signature
from httpx import ASGITransport, AsyncClient
from mail_s34_helpers import mail_db, seed_team
from sqlalchemy import select

_SECRET = "oauth-ingest-shared-secret"


def _headers(raw: bytes, *, ts: int | None = None, secret: str = _SECRET) -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    sig = compute_mail_push_signature(secret=secret, timestamp=ts, raw_body=raw)
    return {
        "Content-Type": "application/json",
        "X-Mail-Signature": f"sha256={sig}",
        "X-Mail-Timestamp": str(ts),
    }


async def _build(monkeypatch: pytest.MonkeyPatch, sm: Any, *, secret: str = _SECRET) -> Any:
    from app.api import deps
    from app.config import get_settings
    from app.main import create_app

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


def _state(*, team_id: uuid.UUID | None, exp_delta: int = 600, secret: str = _SECRET) -> str:
    return encode_crm_state(
        secret=secret,
        team_id=team_id,
        initiator_user_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        exp=int(time.time()) + exp_delta,
    )


def _body(crm_state: str, *, account_id: int = 500, email: str = "box@outlook.com") -> bytes:
    obj = {
        "crm_state": crm_state,
        "mail_account_id": account_id,
        "email": email,
        "display_name": "Иван Пётр",
        "is_active": True,
    }
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


async def _fetch_account(sm: Any, account_id: int) -> Any:
    from app.models.mail_account import MailAccount

    async with sm() as s:
        return (
            await s.execute(select(MailAccount).where(MailAccount.id == account_id))
        ).scalar_one_or_none()


# ------------------------------------------------- 503: пустой MAIL_PUSH_SECRET
async def test_ingest_503_when_secret_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm, secret="")
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=b'{"garbage')
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "mail_ingest_not_configured"


# --------------------------------------------------------- 401: подпись/skew
async def test_ingest_401_tampered_outer_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm)
        raw = _body(_state(team_id=None))
        headers = _headers(raw)
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=raw + b" ", headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize("delta", [400, -400])
async def test_ingest_401_skew_out_of_window(monkeypatch: pytest.MonkeyPatch, delta: int) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm)
        raw = _body(_state(team_id=None))
        headers = _headers(raw, ts=int(time.time()) + delta)
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=raw, headers=headers)
    assert resp.status_code == 401


async def test_ingest_malformed_without_signature_is_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Аутентификация раньше парсинга: битое тело без подписи → 401, не 400."""
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm)
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=b"{not json")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


# ------------------------------------------ 400: битое тело с валидной подписью
async def test_ingest_400_malformed_body_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm)
        raw = b'{"mail_account_id": not-valid-json}'
        headers = _headers(raw)
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=raw, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


# ------------------------- 401: битый crm_state HMAC (внешний HMAC валиден)
async def test_ingest_401_bad_crm_state_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm)
        good = _state(team_id=None)
        body, _, sig = good.partition(".")
        broken = f"{body}.{sig[:-1]}{'0' if sig[-1] != '0' else '1'}"
        raw = _body(broken)
        headers = _headers(raw)  # внешний HMAC валиден
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=raw, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


# ------------------------------- 410: протухший crm_state → oauth_state_expired
async def test_ingest_410_expired_crm_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        app = await _build(monkeypatch, sm)
        raw = _body(_state(team_id=None, exp_delta=-10))  # exp в прошлом
        headers = _headers(raw)
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=raw, headers=headers)
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "oauth_state_expired"


# ---------------------------------- 200: успешный upsert (team_id из crm_state)
async def test_ingest_200_upsert_uses_team_from_state(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            team = await seed_team(s)
            team_id = team.id
        app = await _build(monkeypatch, sm)
        raw = _body(_state(team_id=team_id), account_id=700, email="me@outlook.com")
        headers = _headers(raw)
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=raw, headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}
        acc = await _fetch_account(sm, 700)
    assert acc is not None
    assert acc.email == "me@outlook.com"
    assert acc.display_name == "Иван Пётр"
    assert acc.team_id == team_id
    assert acc.is_active is True


# ------------------- безопасность: спуф team_id в теле игнорируется (берётся из state)
async def test_ingest_team_id_spoof_in_body_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            signed_team = await seed_team(s)
            spoof_team = await seed_team(s)
            signed_id = signed_team.id
            spoof_id = spoof_team.id
        app = await _build(monkeypatch, sm)
        # crm_state несёт signed_id; в тело кладём чужой team_id — он не в схеме, игнорируется.
        obj = {
            "crm_state": _state(team_id=signed_id),
            "mail_account_id": 710,
            "email": "spoof@outlook.com",
            "display_name": None,
            "is_active": True,
            "team_id": str(spoof_id),  # спуф — не поле схемы MailOauthIngestRequest
        }
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        headers = _headers(raw)
        async with _client(app) as c:
            resp = await c.post("/api/mail/oauth/ingest", content=raw, headers=headers)
        assert resp.status_code == 200, resp.text
        acc = await _fetch_account(sm, 710)
    assert acc is not None
    assert acc.team_id == signed_id  # из подписанного state, НЕ спуф
    assert acc.team_id != spoof_id


# --------------------------- re-consent того же id: без дубля, team_id перезаписан
async def test_ingest_reconsent_overwrites_team_no_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            team_a = await seed_team(s)
            team_b = await seed_team(s)
            id_a = team_a.id
            id_b = team_b.id
        app = await _build(monkeypatch, sm)
        # Первый консент → team_a.
        raw1 = _body(_state(team_id=id_a), account_id=720, email="rc@outlook.com")
        # Re-consent того же ящика (id 720) → team_b, новое имя.
        obj2 = {
            "crm_state": _state(team_id=id_b),
            "mail_account_id": 720,
            "email": "rc-renamed@outlook.com",
            "display_name": "Renamed",
            "is_active": False,
        }
        raw2 = json.dumps(obj2, ensure_ascii=False).encode("utf-8")
        async with _client(app) as c:
            r1 = await c.post("/api/mail/oauth/ingest", content=raw1, headers=_headers(raw1))
            r2 = await c.post("/api/mail/oauth/ingest", content=raw2, headers=_headers(raw2))
        assert r1.status_code == 200 and r2.status_code == 200

        from app.models.mail_account import MailAccount

        async with sm() as s:
            rows = (
                (await s.execute(select(MailAccount).where(MailAccount.id == 720))).scalars().all()
            )
    assert len(rows) == 1  # без дубля
    acc = rows[0]
    assert acc.team_id == id_b  # детерминированно перезаписан
    assert acc.email == "rc-renamed@outlook.com"
    assert acc.display_name == "Renamed"
    assert acc.is_active is False


# ------------------------------------ идемпотентность: повтор идентичного ingest
async def test_ingest_idempotent_repeat(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            team = await seed_team(s)
            team_id = team.id
        app = await _build(monkeypatch, sm)
        state = _state(team_id=team_id)
        raw = _body(state, account_id=730)
        async with _client(app) as c:
            r1 = await c.post("/api/mail/oauth/ingest", content=raw, headers=_headers(raw))
            r2 = await c.post("/api/mail/oauth/ingest", content=raw, headers=_headers(raw))
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json() == {"ok": True} and r2.json() == {"ok": True}

        from app.models.mail_account import MailAccount

        async with sm() as s:
            rows = (
                (await s.execute(select(MailAccount).where(MailAccount.id == 730))).scalars().all()
            )
    assert len(rows) == 1  # повтор не создал дубля


# ------------------------- безопасность: crm_state и тело не попадают в логи
async def test_ingest_crm_state_not_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    async with mail_db() as sm:
        async with sm() as s, s.begin():
            team = await seed_team(s)
            team_id = team.id
        app = await _build(monkeypatch, sm)
        state = _state(team_id=team_id)
        raw = _body(state, account_id=740)
        with structlog.testing.capture_logs() as logs:
            async with _client(app) as c:
                resp = await c.post("/api/mail/oauth/ingest", content=raw, headers=_headers(raw))
        assert resp.status_code == 200, resp.text
        for event in logs:
            for value in event.values():
                assert state not in str(value)
