"""Integration-тесты ADR-040: reveal секретов бэка + reverse-lookup + FK-валидация.

Реальный Postgres + реальные сервисы. Покрывают: reveal api-key/admin-api-key
(200 value==decrypt, Cache-Control:no-store, аудит secret_revealed без значения,
404 backend_not_found, 404 secret_not_set при has_*=false, 403 без backends:edit,
секрет не в list/логах); reverse-lookup GET /api/servers|ai-keys/{id}/backends
(состав, сортировка, 404); backend_count в AiKeyListItem; POST/PATCH с несуществующим
server_id/ai_key_id → 422 details[].field; presence-семантика PATCH FK/секретов/git/note.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.infra.crypto import encrypt_secret
from app.models.ai_key import AiKey
from app.models.server import Server
from app.models.service_backend import Backend
from sms_helpers import build_app, build_principal, client, sms_db
from sqlalchemy.ext.asyncio import AsyncSession

_API_KEY = "bk-api-PROBE-111"
_ADMIN_KEY = "bk-admin-PROBE-222"


async def _seed_backend(
    session: AsyncSession,
    *,
    code: str = "api-eu",
    domain: str = "https://api.example.com/",
    server_id: uuid.UUID | None = None,
    ai_key_id: uuid.UUID | None = None,
    with_api_key: bool = True,
    with_admin_key: bool = True,
    position: int = 0,
) -> Backend:
    backend = Backend(
        code=code,
        name=f"Backend {code}",
        domain=domain,
        server_id=server_id,
        ai_key_id=ai_key_id,
        api_key_encrypted=encrypt_secret(_API_KEY) if with_api_key else None,
        admin_api_key_encrypted=encrypt_secret(_ADMIN_KEY) if with_admin_key else None,
        position=position,
    )
    session.add(backend)
    await session.flush()
    return backend


async def _seed_server(session: AsyncSession) -> Server:
    server = Server(
        name="srv", ip="10.7.7.7", ssh_user="root", ssh_password_encrypted=encrypt_secret("x")
    )
    session.add(server)
    await session.flush()
    return server


async def _seed_ai_key(session: AsyncSession) -> AiKey:
    ai_key = AiKey(name="k", provider="openai", key_encrypted=encrypt_secret("sk-x"))
    session.add(ai_key)
    await session.flush()
    return ai_key


# --- reveal ------------------------------------------------------------------


class _RecordingLogger:
    """Детерминированный перехват аудита (без structlog capture_logs — тот флейкает
    из-за кэширования module-логгера при cache_logger_on_first_use=True)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def error(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


async def test_reveal_backend_api_key_and_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.infra.audit as audit_mod

    rec = _RecordingLogger()
    monkeypatch.setattr(audit_mod, "logger", rec)

    async with sms_db() as sm:
        async with sm() as s:
            backend = await _seed_backend(s)
            bid = backend.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            api = await c.get(f"/api/backends/{bid}/api-key")
            admin = await c.get(f"/api/backends/{bid}/admin-api-key")

    assert api.status_code == 200
    assert api.json() == {"value": _API_KEY}
    assert api.headers["cache-control"] == "no-store"
    assert admin.status_code == 200
    assert admin.json() == {"value": _ADMIN_KEY}
    assert admin.headers["cache-control"] == "no-store"
    audit = [kw for ev, kw in rec.events if ev == "secret_revealed"]
    assert len(audit) == 2
    assert {e["resource_type"] for e in audit} == {"backend"}
    assert all(e["resource_id"] == str(bid) for e in audit)
    assert _API_KEY not in str(rec.events) and _ADMIN_KEY not in str(rec.events)


async def test_reveal_backend_without_key_is_404_secret_not_set() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            backend = await _seed_backend(s, with_api_key=False, with_admin_key=False)
            bid = backend.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            api = await c.get(f"/api/backends/{bid}/api-key")
            admin = await c.get(f"/api/backends/{bid}/admin-api-key")
    assert api.status_code == 404 and api.json()["error"]["code"] == "secret_not_set"
    assert admin.status_code == 404 and admin.json()["error"]["code"] == "secret_not_set"


async def test_reveal_backend_missing_is_404_backend_not_found() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get(f"/api/backends/{uuid.uuid4()}/api-key")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "backend_not_found"


async def test_reveal_backend_without_edit_is_403() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            backend = await _seed_backend(s)
            bid = backend.id
            await s.commit()
        viewer = build_principal(is_superadmin=False, permissions={"backends": ["view"]})
        app = build_app(sm, viewer)
        async with client(app) as c:
            resp = await c.get(f"/api/backends/{bid}/api-key")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_backend_secret_not_in_list() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            await _seed_backend(s)
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get("/api/backends")
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["has_api_key"] is True
    assert item["has_admin_api_key"] is True
    assert _API_KEY not in resp.text and _ADMIN_KEY not in resp.text
    assert "api_key" not in item and "admin_api_key" not in item


# --- reverse-lookup ----------------------------------------------------------


async def test_server_backends_reverse_lookup_and_sort() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            server = await _seed_server(s)
            await _seed_backend(s, code="b-late", server_id=server.id, position=1)
            await _seed_backend(s, code="b-early", server_id=server.id, position=0)
            server_id = server.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            ok = await c.get(f"/api/servers/{server_id}/backends")
            missing = await c.get(f"/api/servers/{uuid.uuid4()}/backends")
    assert ok.status_code == 200
    codes = [b["code"] for b in ok.json()["backends"]]
    assert codes == ["b-early", "b-late"]  # position ASC
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "server_not_found"


async def test_ai_key_backends_reverse_lookup_and_count() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            ai_key = await _seed_ai_key(s)
            await _seed_backend(s, code="bk1", ai_key_id=ai_key.id)
            await _seed_backend(s, code="bk2", ai_key_id=ai_key.id)
            ai_key_id = ai_key.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            lookup = await c.get(f"/api/ai-keys/{ai_key_id}/backends")
            missing = await c.get(f"/api/ai-keys/{uuid.uuid4()}/backends")
            keys = await c.get("/api/ai-keys")

    assert lookup.status_code == 200
    assert {b["code"] for b in lookup.json()["backends"]} == {"bk1", "bk2"}
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "ai_key_not_found"
    # backend_count в AiKeyListItem (ADR-040).
    item = next(i for i in keys.json()["items"] if i["id"] == str(ai_key_id))
    assert item["backend_count"] == 2


# --- FK-валидация create -----------------------------------------------------


async def test_create_backend_nonexistent_server_id_is_422() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/backends",
                json={
                    "code": "new-b",
                    "name": "New B",
                    "domain": "api.example.com",
                    "server_id": str(uuid.uuid4()),
                },
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "server_id"


async def test_create_backend_nonexistent_ai_key_id_is_422() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.post(
                "/api/backends",
                json={
                    "code": "new-b2",
                    "name": "New B2",
                    "domain": "api.example.com",
                    "ai_key_id": str(uuid.uuid4()),
                },
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "ai_key_id"


# --- presence-семантика PATCH (FK/секреты/git/note) --------------------------


async def test_patch_backend_sets_fk_secret_git_note() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            server = await _seed_server(s)
            backend = await _seed_backend(s, with_api_key=False, with_admin_key=False)
            bid, server_id = backend.id, server.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            # PATCH без domain → без re-check (сетевой проверки нет).
            patched = await c.patch(
                f"/api/backends/{bid}",
                json={
                    "server_id": str(server_id),
                    "api_key": "secret-x",
                    "git": "https://git/repo",
                    "note": "заметка",
                },
            )
            revealed = await c.get(f"/api/backends/{bid}/api-key")

    assert patched.status_code == 200
    body = patched.json()
    assert body["server_id"] == str(server_id)
    assert body["has_api_key"] is True
    assert body["git"] == "https://git/repo"
    assert body["note"] == "заметка"
    assert revealed.status_code == 200
    assert revealed.json() == {"value": "secret-x"}


async def test_patch_backend_clears_fk_with_null() -> None:
    async with sms_db() as sm:
        async with sm() as s:
            server = await _seed_server(s)
            backend = await _seed_backend(s, server_id=server.id)
            bid = backend.id
            await s.commit()
        app = build_app(sm, build_principal())
        async with client(app) as c:
            patched = await c.patch(f"/api/backends/{bid}", json={"server_id": None})
    assert patched.status_code == 200
    assert patched.json()["server_id"] is None
