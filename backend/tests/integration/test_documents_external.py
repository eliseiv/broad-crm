"""Внешний read-only контур модуля «Документы» (RAG, X-API-Key, ADR-060, 04-api.md#external).

Реальный Postgres. Покрывает: порядок проверок ключа (503 → 401); latin-1 X-API-Key → 401
(не 500); keyset `(updated_at,id)` ASC (стабильность на дублях updated_at, next_cursor,
битый cursor → 400, limit вне 1..500 → 400, updated_after); /changes (since обязателен,
tombstones); GET /{id} (200/410 tombstone/404); /access (is_public/effective/404 на удалённом);
машина видит restricted; Cache-Control: no-store на ответах контура ВКЛЮЧАЯ ошибки.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from documents_helpers import (
    build_app,
    build_principal,
    client,
    documents_db,
    seed_node,
    seed_role,
    set_node_roles,
)
from sqlalchemy import text as sa_text

_KEY = "secret-external-key-123"
_HDR = {"X-API-Key": _KEY}


def _configure_key(monkeypatch: pytest.MonkeyPatch, value: str = _KEY) -> None:
    monkeypatch.setenv("DOCUMENTS_API_KEY", value)
    from app.config import get_settings

    get_settings.cache_clear()


def _app(sm: object) -> object:
    # Принципал не используется внешним контуром (безролевой ключ), но build_app его требует.
    return build_app(sm, build_principal())


# --- Порядок проверок ключа --------------------------------------------------


async def test_empty_key_returns_503_before_auth() -> None:
    """Пустой DOCUMENTS_API_KEY → 503 documents_external_not_configured (до проверки заголовка)."""
    async with documents_db() as sm:  # ключ по умолчанию пуст
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get("/api/external/documents", headers=_HDR)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "documents_external_not_configured"
    assert resp.headers.get("Cache-Control") == "no-store"


async def test_missing_and_wrong_key_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            no_key = await c.get("/api/external/documents")
            wrong = await c.get("/api/external/documents", headers={"X-API-Key": "nope"})
    for resp in (no_key, wrong):
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "not_authenticated"
        assert resp.headers.get("Cache-Control") == "no-store"


async def test_latin1_key_returns_401_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """X-API-Key с не-ASCII байтами (latin-1) → 401, НЕ 500 (compare_digest на bytes)."""
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            # Сырые latin-1 байты (128..255) в заголовке — так Starlette получает не-ASCII
            # `str` при декодировании (передаём bytes, чтобы httpx не кодировал их как ASCII).
            resp = await c.get("/api/external/documents", headers={"X-API-Key": b"\xff\xfe\xfd"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "not_authenticated"


async def test_valid_key_returns_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get("/api/external/documents", headers=_HDR)
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.json() == {"items": [], "next_cursor": None}


# --- Keyset-пагинация --------------------------------------------------------


async def test_keyset_stable_on_duplicate_updated_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Дубли updated_at → пагинация (updated_at,id) без пропусков; next_cursor=null в конце."""
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            for i in range(5):
                await seed_node(s, node_type="document", name=f"D{i}")
            # Форсируем ОДИНАКОВЫЙ updated_at всем узлам → тай-брейк по id.
            await s.execute(
                sa_text("UPDATE document_nodes SET updated_at = '2026-01-01T00:00:00+00'")
            )
            await s.commit()
        app = _app(sm)
        seen: list[str] = []
        async with client(app) as c:
            cursor: str | None = None
            for _ in range(10):  # предохранитель от бесконечного цикла
                url = "/api/external/documents?limit=2"
                if cursor:
                    url += f"&cursor={cursor}"
                page = (await c.get(url, headers=_HDR)).json()
                seen.extend(item["id"] for item in page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
    assert len(seen) == 5
    assert len(set(seen)) == 5  # без дублей и пропусков


async def test_bad_cursor_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get("/api/external/documents?cursor=not-a-valid-cursor", headers=_HDR)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.headers.get("Cache-Control") == "no-store"


async def test_limit_out_of_range_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            too_low = await c.get("/api/external/documents?limit=0", headers=_HDR)
            too_high = await c.get("/api/external/documents?limit=501", headers=_HDR)
    for resp in (too_low, too_high):
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "validation_error"


async def test_updated_after_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            await seed_node(s, node_type="document", name="Old")
            await s.execute(
                sa_text("UPDATE document_nodes SET updated_at = '2020-01-01T00:00:00+00'")
            )
            await s.commit()
        app = _app(sm)
        async with client(app) as c:
            future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
            resp = await c.get(
                "/api/external/documents", params={"updated_after": future}, headers=_HDR
            )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# --- /changes ----------------------------------------------------------------


async def test_changes_requires_since(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get("/api/external/documents/changes", headers=_HDR)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


async def test_changes_includes_tombstones(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            live = await seed_node(s, node_type="document", name="Live")
            gone = await seed_node(
                s,
                node_type="document",
                name="Gone",
                deleted_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            await s.execute(
                sa_text("UPDATE document_nodes SET updated_at = '2026-02-01T00:00:00+00'")
            )
            await s.commit()
            live_id, gone_id = str(live.id), str(gone.id)
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get(
                "/api/external/documents/changes?since=2020-01-01T00:00:00%2B00:00",
                headers=_HDR,
            )
    assert resp.status_code == 200
    items = {item["id"]: item for item in resp.json()["items"]}
    assert live_id in items and gone_id in items
    assert items[gone_id]["deleted_at"] is not None  # tombstone


# --- GET /{id} + /access -----------------------------------------------------


async def test_get_external_live_returns_content_and_effective_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Живой узел → 200 + content_md + ЭФФЕКТИВНЫЙ visibility_role_ids (машина видит restricted)."""
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            folder = await seed_node(s, node_type="folder", name="F", visibility_mode="restricted")
            await set_node_roles(s, folder.id, [role.id])
            doc = await seed_node(
                s, node_type="document", parent_id=folder.id, name="D", content_md="# secret"
            )
            await s.commit()
            doc_id, role_id = str(doc.id), str(role.id)
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get(f"/api/external/documents/{doc_id}", headers=_HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert body["content_md"] == "# secret"
    # inherit-документ под restricted-папкой → эффективный набор = роли папки.
    assert body["visibility_role_ids"] == [role_id]
    assert resp.headers.get("Cache-Control") == "no-store"


async def test_get_external_deleted_returns_410_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            gone = await seed_node(
                s,
                node_type="document",
                name="Gone",
                content_md="x",
                deleted_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
            await s.commit()
            gone_id = str(gone.id)
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get(f"/api/external/documents/{gone_id}", headers=_HDR)
    assert resp.status_code == 410
    err = resp.json()["error"]
    assert err["code"] == "document_node_gone"
    assert err["details"]["id"] == gone_id
    assert err["details"]["deleted_at"] is not None
    assert "content_version" in err["details"]
    assert resp.headers.get("Cache-Control") == "no-store"


async def test_get_external_nonexistent_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get(
                "/api/external/documents/00000000-0000-0000-0000-0000000000ff", headers=_HDR
            )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "document_node_not_found"
    assert resp.headers.get("Cache-Control") == "no-store"


async def test_access_public_and_restricted(monkeypatch: pytest.MonkeyPatch) -> None:
    """/access: публичный → is_public=true,[]; restricted → false + эффективные роли."""
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            public = await seed_node(s, node_type="document", name="Pub")
            restricted = await seed_node(
                s, node_type="folder", name="R", visibility_mode="restricted"
            )
            await set_node_roles(s, restricted.id, [role.id])
            await s.commit()
            public_id, restricted_id, role_id = str(public.id), str(restricted.id), str(role.id)
        app = _app(sm)
        async with client(app) as c:
            pub = (await c.get(f"/api/external/documents/{public_id}/access", headers=_HDR)).json()
            res = (
                await c.get(f"/api/external/documents/{restricted_id}/access", headers=_HDR)
            ).json()
    assert pub["is_public"] is True
    assert pub["visibility_role_ids"] == []
    assert "content_version" in pub
    assert res["is_public"] is False
    assert res["visibility_role_ids"] == [role_id]


async def test_access_deleted_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            gone = await seed_node(
                s, node_type="document", name="Gone", deleted_at=datetime(2026, 1, 3, tzinfo=UTC)
            )
            await s.commit()
            gone_id = str(gone.id)
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get(f"/api/external/documents/{gone_id}/access", headers=_HDR)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "document_node_not_found"


async def test_machine_sees_restricted_nodes_in_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Внешний список несёт restricted-узлы (обход per-role фильтра)."""
    _configure_key(monkeypatch)
    async with documents_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            restricted = await seed_node(
                s, node_type="document", name="R", visibility_mode="restricted"
            )
            await set_node_roles(s, restricted.id, [role.id])
            await s.commit()
            restricted_id, role_id = str(restricted.id), str(role.id)
        app = _app(sm)
        async with client(app) as c:
            resp = await c.get("/api/external/documents", headers=_HDR)
    ids = {item["id"]: item for item in resp.json()["items"]}
    assert restricted_id in ids
    assert ids[restricted_id]["visibility_role_ids"] == [role_id]


# --- Cache-Control на не-external -------------------------------------------


async def test_internal_endpoints_have_no_no_store() -> None:
    """`Cache-Control: no-store` — ТОЛЬКО на внешнем контуре, не на внутренних эндпоинтах."""
    async with documents_db() as sm:
        app = build_app(sm, build_principal())
        async with client(app) as c:
            resp = await c.get("/api/documents/tree")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") != "no-store"
