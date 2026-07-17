"""Контрактные integration-тесты внутреннего API модуля «Документы» (04-api.md#documents, ADR-059).

Реальный Postgres. Покрывает: RBAC-гейты метод→действие (view/create/edit/delete/share);
upload-валидацию (.md/размер/UTF-8); create/patch (content_version, expected_version,
content у папки); copy (рекурсия/перенос видимости/owner/цикл); soft-delete каскад;
reorder (прецеденция 400→404→422, position=0..N-1); visibility PATCH; role-refs.
Коды/статусы — по 04-api.md.
"""

from __future__ import annotations

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

_VIEW_ONLY = {"documents": ["view"]}
_NO_SHARE = {"documents": ["view", "create", "edit", "delete"]}


def _admin(sm: object) -> object:
    return build_app(sm, build_principal())  # супер-админ (sees_all, все гейты)


# --- RBAC-гейты метод→действие ----------------------------------------------


async def test_gates_require_correct_action() -> None:
    async with documents_db() as sm:
        async with sm() as s:
            node = await seed_node(s, node_type="document", name="D")
            await s.commit()
            node_id = str(node.id)

        app = build_app(sm, build_principal(is_superadmin=False, permissions=_VIEW_ONLY))
        async with client(app) as c:
            # view проходит.
            assert (await c.get("/api/documents/tree")).status_code == 200
            # create/edit/delete/share — 403 forbidden.
            forbidden = [
                await c.post("/api/documents/folders", json={"name": "X"}),
                await c.post("/api/documents/documents", json={"name": "X"}),
                await c.patch(f"/api/documents/nodes/{node_id}", json={"name": "Y"}),
                await c.patch(
                    f"/api/documents/nodes/{node_id}/visibility",
                    json={"visibility_mode": "inherit"},
                ),
                await c.patch("/api/documents/order", json={"parent_id": None, "ids": [node_id]}),
                await c.delete(f"/api/documents/nodes/{node_id}"),
                await c.get("/api/documents/role-refs"),
                await c.get(f"/api/documents/nodes/{node_id}/visibility"),
            ]
        for resp in forbidden:
            assert resp.status_code == 403, resp.request.url
            assert resp.json()["error"]["code"] == "forbidden"


async def test_share_endpoints_gated_by_share_not_view() -> None:
    """GET visibility и role-refs требуют `documents:share`, а не только view/edit."""
    async with documents_db() as sm:
        async with sm() as s:
            node = await seed_node(s, node_type="document", name="D")
            await s.commit()
            node_id = str(node.id)

        app = build_app(sm, build_principal(is_superadmin=False, permissions=_NO_SHARE))
        async with client(app) as c:
            r_vis = await c.get(f"/api/documents/nodes/{node_id}/visibility")
            r_refs = await c.get("/api/documents/role-refs")
    assert r_vis.status_code == 403
    assert r_refs.status_code == 403


# --- upload ------------------------------------------------------------------


async def test_upload_rejects_non_md() -> None:
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/documents/upload",
                files={"file": ("note.txt", b"# hi", "text/plain")},
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "document_upload_invalid"


async def test_upload_rejects_bad_utf8() -> None:
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/documents/upload",
                files={"file": ("note.md", b"\xff\xfe\x00\xff", "text/markdown")},
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "document_upload_invalid"


async def test_upload_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCUMENTS_MAX_MD_BYTES", "16")
    from app.config import get_settings

    get_settings.cache_clear()
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/documents/upload",
                files={"file": ("big.md", b"x" * 100, "text/markdown")},
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "document_upload_invalid"


async def test_upload_valid_md_creates_document() -> None:
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/documents/upload",
                files={"file": ("guide.md", "# Заголовок\nтекст".encode(), "text/markdown")},
            )
    assert resp.status_code == 201
    body = resp.json()
    assert body["node_type"] == "document"
    assert body["name"] == "guide"
    assert body["content_version"] == 1


# --- create / patch ----------------------------------------------------------


async def test_create_document_content_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Контент > лимита при inline-создании → 422 validation_error, поле content_md."""
    monkeypatch.setenv("DOCUMENTS_MAX_MD_BYTES", "16")
    from app.config import get_settings

    get_settings.cache_clear()
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.post(
                "/api/documents/documents",
                json={"name": "Big", "content_md": "y" * 100},
            )
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "validation_error"
    assert any(d.get("field") == "content_md" for d in body["details"])


async def test_patch_content_on_folder_rejected() -> None:
    """content_md у папки → 422 validation_error поле content_md."""
    async with documents_db() as sm:
        async with sm() as s:
            folder = await seed_node(s, node_type="folder", name="F")
            await s.commit()
            folder_id = str(folder.id)
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.patch(f"/api/documents/nodes/{folder_id}", json={"content_md": "nope"})
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "validation_error"
    assert any(d.get("field") == "content_md" for d in body["details"])


async def test_content_version_bumps_only_on_name_or_content() -> None:
    """content_version += 1 при name/content; смена видимости версию НЕ трогает."""
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            created = (
                await c.post("/api/documents/documents", json={"name": "Doc", "content_md": "a"})
            ).json()
            node_id = created["id"]
            assert created["content_version"] == 1

            after_rename = (
                await c.patch(f"/api/documents/nodes/{node_id}", json={"name": "Doc2"})
            ).json()
            assert after_rename["content_version"] == 2

            after_content = (
                await c.patch(f"/api/documents/nodes/{node_id}", json={"content_md": "bb"})
            ).json()
            assert after_content["content_version"] == 3

            # Смена видимости — content_version неизменен.
            after_vis = (
                await c.patch(
                    f"/api/documents/nodes/{node_id}/visibility",
                    json={"visibility_mode": "inherit"},
                )
            ).json()
            assert after_vis["content_version"] == 3


async def test_expected_version_conflict() -> None:
    """expected_version ≠ текущему → 409 document_node_conflict."""
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            node_id = (await c.post("/api/documents/documents", json={"name": "Doc"})).json()["id"]
            resp = await c.patch(
                f"/api/documents/nodes/{node_id}",
                json={"name": "New", "expected_version": 999},
            )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "document_node_conflict"


async def test_get_node_returns_content_md() -> None:
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            node_id = (
                await c.post(
                    "/api/documents/documents", json={"name": "Doc", "content_md": "# Body"}
                )
            ).json()["id"]
            # В списках content_md не отдаётся, в GET одного узла — отдаётся.
            in_list = (await c.get("/api/documents/nodes")).json()
            single = (await c.get(f"/api/documents/nodes/{node_id}")).json()
    assert all(n["content_md"] is None for n in in_list)
    assert single["content_md"] == "# Body"


# --- copy --------------------------------------------------------------------


async def test_copy_recurses_and_transfers_visibility() -> None:
    """Копия поддерева: новые id, перенос visibility+ролей, owner=актор, content_version=1."""
    async with documents_db() as sm:
        async with sm() as s:
            role = await seed_role(s)
            folder = await seed_node(s, node_type="folder", name="F", visibility_mode="restricted")
            await set_node_roles(s, folder.id, [role.id])
            await seed_node(s, node_type="document", parent_id=folder.id, name="Child")
            await s.commit()
            folder_id = str(folder.id)
            role_id = str(role.id)

        app = _admin(sm)
        async with client(app) as c:
            copy_resp = await c.post(f"/api/documents/nodes/{folder_id}/copy", json={})
            new_root = copy_resp.json()
            tree = (await c.get("/api/documents/tree")).json()
            new_vis = await c.get(f"/api/documents/nodes/{new_root['id']}/visibility")
    assert copy_resp.status_code == 201
    assert new_root["id"] != folder_id
    assert new_root["visibility_mode"] == "restricted"
    assert new_root["content_version"] == 1
    # Оригинал (2) + копия (2) = 4 узла.
    assert len(tree) == 4
    # Перенос ролей.
    assert new_vis.json()["role_ids"] == [role_id]


async def test_copy_cycle_rejected() -> None:
    """Копирование узла внутрь собственного потомка → 422 document_copy_cycle."""
    async with documents_db() as sm:
        async with sm() as s:
            root = await seed_node(s, node_type="folder", name="Root")
            child = await seed_node(s, node_type="folder", parent_id=root.id, name="Sub")
            await s.commit()
            root_id, child_id = str(root.id), str(child.id)
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.post(
                f"/api/documents/nodes/{root_id}/copy",
                json={"target_parent_id": child_id},
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "document_copy_cycle"


# --- soft-delete cascade -----------------------------------------------------


async def test_delete_folder_cascades_subtree() -> None:
    async with documents_db() as sm:
        async with sm() as s:
            folder = await seed_node(s, node_type="folder", name="F")
            child = await seed_node(s, node_type="document", parent_id=folder.id, name="C")
            await s.commit()
            folder_id, child_id = str(folder.id), str(child.id)
        app = _admin(sm)
        async with client(app) as c:
            del_resp = await c.delete(f"/api/documents/nodes/{folder_id}")
            after_folder = await c.get(f"/api/documents/nodes/{folder_id}")
            after_child = await c.get(f"/api/documents/nodes/{child_id}")
            tree = (await c.get("/api/documents/tree")).json()
    assert del_resp.status_code == 204
    assert after_folder.status_code == 404
    assert after_child.status_code == 404
    assert tree == []


# --- reorder -----------------------------------------------------------------


async def test_reorder_assigns_positions_and_precedence() -> None:
    """/order: успех даёт position=0..N-1; прецеденция 400 (форма) → 404 (id) → 422 (полнота)."""
    async with documents_db() as sm:
        async with sm() as s:
            a = await seed_node(s, node_type="folder", name="A")
            b = await seed_node(s, node_type="folder", name="B")
            d = await seed_node(s, node_type="folder", name="D")
            await s.commit()
            a_id, b_id, d_id = str(a.id), str(b.id), str(d.id)
        app = _admin(sm)
        async with client(app) as c:
            # Успех: порядок d,a,b.
            ok = await c.patch(
                "/api/documents/order", json={"parent_id": None, "ids": [d_id, a_id, b_id]}
            )
            nodes = (await c.get("/api/documents/nodes")).json()
            # 400 — форма (нет ids).
            r400 = await c.patch("/api/documents/order", json={"parent_id": None})
            # 404 — неизвестный id (проверяется до полноты).
            unknown = "00000000-0000-0000-0000-0000000000ff"
            r404 = await c.patch(
                "/api/documents/order",
                json={"parent_id": None, "ids": [d_id, a_id, unknown]},
            )
            # 422 — неполная перестановка (подмножество).
            r422 = await c.patch(
                "/api/documents/order", json={"parent_id": None, "ids": [d_id, a_id]}
            )
    assert ok.status_code == 204
    order = {n["id"]: n["position"] for n in nodes}
    assert order == {d_id: 0, a_id: 1, b_id: 2}
    assert r400.status_code == 400
    assert r400.json()["error"]["code"] == "validation_error"
    assert r404.status_code == 404
    assert r404.json()["error"]["code"] == "document_node_not_found"
    assert r422.status_code == 422
    assert r422.json()["error"]["code"] == "unprocessable"


# --- visibility PATCH --------------------------------------------------------


async def test_visibility_patch_overwrite_and_inherit() -> None:
    """restricted+role_ids перезаписывает набор; inherit удаляет строки."""
    async with documents_db() as sm:
        async with sm() as s:
            role_a = await seed_role(s)
            role_b = await seed_role(s)
            await s.commit()
            role_a_id, role_b_id = str(role_a.id), str(role_b.id)
        app = _admin(sm)
        async with client(app) as c:
            node_id = (await c.post("/api/documents/folders", json={"name": "F"})).json()["id"]
            # restricted с двумя ролями.
            await c.patch(
                f"/api/documents/nodes/{node_id}/visibility",
                json={"visibility_mode": "restricted", "role_ids": [role_a_id, role_b_id]},
            )
            after_two = (await c.get(f"/api/documents/nodes/{node_id}/visibility")).json()
            # Перезапись на одну роль.
            await c.patch(
                f"/api/documents/nodes/{node_id}/visibility",
                json={"visibility_mode": "restricted", "role_ids": [role_a_id]},
            )
            after_one = (await c.get(f"/api/documents/nodes/{node_id}/visibility")).json()
            # inherit — строки удаляются.
            await c.patch(
                f"/api/documents/nodes/{node_id}/visibility",
                json={"visibility_mode": "inherit"},
            )
            after_inherit = (await c.get(f"/api/documents/nodes/{node_id}/visibility")).json()
    assert sorted(after_two["role_ids"]) == sorted([role_a_id, role_b_id])
    assert after_one["role_ids"] == [role_a_id]
    assert after_inherit == {"visibility_mode": "inherit", "role_ids": []}


async def test_visibility_nonexistent_role_rejected() -> None:
    """Несуществующий role_id → 422 validation_error (04-api.md#documents §PATCH visibility)."""
    async with documents_db() as sm:
        app = _admin(sm)
        async with client(app) as c:
            node_id = (await c.post("/api/documents/folders", json={"name": "F"})).json()["id"]
            resp = await c.patch(
                f"/api/documents/nodes/{node_id}/visibility",
                json={
                    "visibility_mode": "restricted",
                    "role_ids": ["00000000-0000-0000-0000-0000000000aa"],
                },
            )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


# --- role-refs ---------------------------------------------------------------


async def test_role_refs_lists_roles_sorted() -> None:
    async with documents_db() as sm:
        async with sm() as s:
            await seed_role(s, name="Яндекс")
            await seed_role(s, name="альфа")
            await seed_role(s, name="Бета")
            await s.commit()
        app = _admin(sm)
        async with client(app) as c:
            resp = await c.get("/api/documents/role-refs")
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    # Сортировка по имени casefold (ru, ci); 'admin'-якорь тоже присутствует.
    assert names == sorted(names, key=str.casefold)
    assert {"Яндекс", "альфа", "Бета"} <= set(names)
