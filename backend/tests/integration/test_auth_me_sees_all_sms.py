"""Тест производного флага `sees_all_sms_teams` в GET /api/auth/me (ADR-036).

`sees_all_sms_teams = is_superadmin OR permissions_subset(full_catalog, permissions)` —
backend единственный источник (фронт не дублирует). True для супер-админа и роли с
полным каталогом прав; False для неполного каталога.
"""

from __future__ import annotations

from sms_helpers import build_app, build_principal, client, sms_db


async def test_me_sees_all_sms_teams_true_for_superadmin() -> None:
    async with sms_db() as sm:
        app = build_app(sm, build_principal())  # супер-админ
        async with client(app) as c:
            resp = await c.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["sees_all_sms_teams"] is True


async def test_me_sees_all_sms_teams_true_for_full_catalog_role() -> None:
    async with sms_db() as sm:
        # БД-пользователь (не супер-админ) с полным каталогом → admin-уровень.
        app = build_app(sm, build_principal(is_superadmin=False))  # permissions=full_catalog
        async with client(app) as c:
            resp = await c.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["sees_all_sms_teams"] is True


async def test_me_sees_all_sms_teams_false_for_partial_catalog() -> None:
    async with sms_db() as sm:
        partial = build_principal(is_superadmin=False, permissions={"sms": ["view"]})
        app = build_app(sm, partial)
        async with client(app) as c:
            resp = await c.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["sees_all_sms_teams"] is False
