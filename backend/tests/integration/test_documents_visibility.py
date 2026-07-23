"""Резолюция видимости по ролям + анти-энумерация модуля «Документы» (ADR-059, 05-security.md).

Реальный Postgres (рекурсивный CTE резолюции). Покрывает: restricted-узел виден роли из
набора и невидим прочим; наследование inherit-потомком; override потомком; публичный узел
(inherit до корня) виден всем с `documents:view`; admin-уровень (superadmin / полный каталог)
видит всё; анти-энумерация — невидимый узел по id → 404 `document_node_not_found` (НЕ 403);
`GET /nodes/{id}/visibility` отдаёт СОБСТВЕННЫЕ роли узла.
"""

from __future__ import annotations

from documents_helpers import (
    build_app,
    build_principal,
    client,
    documents_db,
    seed_node,
    seed_role,
    set_node_roles,
)

# Полный набор действий документов, но НЕ полный каталог прав ⇒ non-admin (sees_all=False).
_DOC_ALL = {"documents": ["view", "create", "edit", "delete", "share"]}


async def test_restricted_node_visible_only_to_its_role_and_inherit_children() -> None:
    """restricted-узел R(roles=[A]) + inherit-потомок D: роль A видит оба, роль B — ничего."""
    async with documents_db() as sm:
        async with sm() as s:
            role_a = await seed_role(s)
            role_b = await seed_role(s)
            root = await seed_node(s, name="R", visibility_mode="restricted")
            await set_node_roles(s, root.id, [role_a.id])
            child = await seed_node(s, node_type="document", parent_id=root.id, name="D")
            await s.commit()
            root_id, child_id = str(root.id), str(child.id)
            role_a_id, role_b_id = role_a.id, role_b.id

        # Роль A — видит R и D.
        app_a = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=role_a_id)
        )
        async with client(app_a) as c:
            tree_a = await c.get("/api/documents/tree")
            node_a = await c.get(f"/api/documents/nodes/{child_id}")
        # Роль B — не видит ничего.
        app_b = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=role_b_id)
        )
        async with client(app_b) as c:
            tree_b = await c.get("/api/documents/tree")
            node_b = await c.get(f"/api/documents/nodes/{child_id}")

    assert tree_a.status_code == 200
    assert {n["id"] for n in tree_a.json()} == {root_id, child_id}
    assert node_a.status_code == 200
    assert tree_b.status_code == 200
    assert tree_b.json() == []
    assert node_b.status_code == 404
    assert node_b.json()["error"]["code"] == "document_node_not_found"


async def test_descendant_override_restricted() -> None:
    """Потомок C(restricted, roles=[B]) под R(restricted, roles=[A]) переопределяет видимость."""
    async with documents_db() as sm:
        async with sm() as s:
            role_a = await seed_role(s)
            role_b = await seed_role(s)
            root = await seed_node(s, name="R", visibility_mode="restricted")
            await set_node_roles(s, root.id, [role_a.id])
            override = await seed_node(s, parent_id=root.id, name="C", visibility_mode="restricted")
            await set_node_roles(s, override.id, [role_b.id])
            leaf = await seed_node(s, node_type="document", parent_id=override.id, name="D2")
            await s.commit()
            override_id, leaf_id = str(override.id), str(leaf.id)
            role_a_id, role_b_id = role_a.id, role_b.id

        app_a = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=role_a_id)
        )
        async with client(app_a) as c:
            a_override = await c.get(f"/api/documents/nodes/{override_id}")
            a_leaf = await c.get(f"/api/documents/nodes/{leaf_id}")
        app_b = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=role_b_id)
        )
        async with client(app_b) as c:
            b_override = await c.get(f"/api/documents/nodes/{override_id}")
            b_leaf = await c.get(f"/api/documents/nodes/{leaf_id}")

    # A управляет R, но C/D2 переопределены на роль B → A их не видит.
    assert a_override.status_code == 404
    assert a_leaf.status_code == 404
    # B видит C и D2 (набор роли B), но НЕ корень R.
    assert b_override.status_code == 200
    assert b_leaf.status_code == 200


async def test_public_node_visible_to_all_with_view() -> None:
    """Ветка полностью inherit до корня ⇒ узел публичен: виден любой роли с documents:view."""
    async with documents_db() as sm:
        async with sm() as s:
            role_a = await seed_role(s)
            role_b = await seed_role(s)
            pub_folder = await seed_node(s, name="Pub", visibility_mode="inherit")
            pub_doc = await seed_node(s, node_type="document", parent_id=pub_folder.id, name="P")
            await s.commit()
            pub_doc_id = str(pub_doc.id)
            role_a_id, role_b_id = role_a.id, role_b.id

        for role_id in (role_a_id, role_b_id):
            app = build_app(
                sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=role_id)
            )
            async with client(app) as c:
                resp = await c.get(f"/api/documents/nodes/{pub_doc_id}")
            assert resp.status_code == 200, role_id


async def test_admin_level_sees_all() -> None:
    """superadmin и роль с полным каталогом видят restricted-узел чужой роли."""
    async with documents_db() as sm:
        async with sm() as s:
            role_a = await seed_role(s)
            secret = await seed_node(s, name="Secret", visibility_mode="restricted")
            await set_node_roles(s, secret.id, [role_a.id])
            await s.commit()
            secret_id = str(secret.id)

        # superadmin.
        app_super = build_app(sm, build_principal())  # is_superadmin=True
        async with client(app_super) as c:
            r_super = await c.get(f"/api/documents/nodes/{secret_id}")
        # Роль с полным каталогом (не superadmin, но sees_all по предикату).
        app_full = build_app(sm, build_principal(is_superadmin=False))  # full_catalog по умолчанию
        async with client(app_full) as c:
            r_full = await c.get(f"/api/documents/nodes/{secret_id}")

    assert r_super.status_code == 200
    assert r_full.status_code == 200


async def test_anti_enumeration_mutations_return_404_not_403() -> None:
    """PATCH/DELETE/copy/visibility невидимого узла → 404 document_node_not_found (не 403)."""
    async with documents_db() as sm:
        async with sm() as s:
            role_a = await seed_role(s)
            role_b = await seed_role(s)
            secret = await seed_node(
                s, node_type="document", name="S", visibility_mode="restricted"
            )
            await set_node_roles(s, secret.id, [role_a.id])
            await s.commit()
            secret_id = str(secret.id)
            role_b_id = role_b.id

        # У пользователя роли B ЕСТЬ все права documents (гейт пройдёт) — но узел невидим.
        app = build_app(
            sm, build_principal(is_superadmin=False, permissions=_DOC_ALL, role_id=role_b_id)
        )
        async with client(app) as c:
            r_get = await c.get(f"/api/documents/nodes/{secret_id}")
            r_patch = await c.patch(f"/api/documents/nodes/{secret_id}", json={"name": "hack"})
            r_vis = await c.patch(
                f"/api/documents/nodes/{secret_id}/visibility",
                json={"visibility_mode": "inherit"},
            )
            r_copy = await c.post(f"/api/documents/nodes/{secret_id}/copy", json={})
            r_del = await c.delete(f"/api/documents/nodes/{secret_id}")

    for resp in (r_get, r_patch, r_vis, r_copy, r_del):
        assert resp.status_code == 404, resp.request.url
        assert resp.json()["error"]["code"] == "document_node_not_found"


async def test_get_visibility_returns_own_role_ids_share_gate() -> None:
    """GET /nodes/{id}/visibility → СОБСТВЕННЫЕ роли restricted-узла; inherit → []."""
    async with documents_db() as sm:
        async with sm() as s:
            role_a = await seed_role(s)
            role_b = await seed_role(s)
            restricted = await seed_node(s, name="R", visibility_mode="restricted")
            await set_node_roles(s, restricted.id, [role_a.id, role_b.id])
            inherit = await seed_node(s, parent_id=restricted.id, name="Inh")
            await s.commit()
            restricted_id, inherit_id = str(restricted.id), str(inherit.id)
            expected = sorted([str(role_a.id), str(role_b.id)])

        app = build_app(sm, build_principal())  # admin — проходит share-гейт и видит всё
        async with client(app) as c:
            r_restricted = await c.get(f"/api/documents/nodes/{restricted_id}/visibility")
            r_inherit = await c.get(f"/api/documents/nodes/{inherit_id}/visibility")

    assert r_restricted.status_code == 200
    body = r_restricted.json()
    assert body["visibility_mode"] == "restricted"
    assert sorted(body["role_ids"]) == expected
    assert r_inherit.status_code == 200
    assert r_inherit.json() == {"visibility_mode": "inherit", "role_ids": [], "rag_exclude": False}


async def test_view_gate_forbidden_without_permission() -> None:
    """Нет `documents:view` → 403 forbidden (RBAC-гейт, ДО per-node фильтра)."""
    async with documents_db() as sm:
        app = build_app(sm, build_principal(is_superadmin=False, permissions={"servers": ["view"]}))
        async with client(app) as c:
            resp = await c.get("/api/documents/tree")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
