"""Integration S4 (ADR-044 §2, MAJOR-4): opt-out `/api/mail/me/settings`.

`GET` дефолт «включено» (нет строки); `PATCH` upsert по `principal.user_id`; повторный
`GET` отражает изменение. Супер-админ без `uid` → 403. Гейт `mail:view`.
"""

from __future__ import annotations

from mail_s34_helpers import build_app, build_principal, client, mail_db, seed_role, seed_user


async def test_get_default_enabled() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role)
            await s.commit()
            uid = user.id
        principal = build_principal(
            user_id=uid, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/mail/me/settings")
    assert resp.status_code == 200
    assert resp.json()["tg_notifications_enabled"] is True  # дефолт (нет строки)


async def test_patch_then_get_reflects_optout() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": ["view"]})
            user = await seed_user(s, role)
            await s.commit()
            uid = user.id
        principal = build_principal(
            user_id=uid, is_superadmin=False, role="member", permissions={"mail": ["view"]}
        )
        app = build_app(sm, principal)
        async with client(app) as c:
            patched = await c.patch(
                "/api/mail/me/settings", json={"tg_notifications_enabled": False}
            )
            fetched = await c.get("/api/mail/me/settings")
            # Повторный upsert обратно в True.
            reenabled = await c.patch(
                "/api/mail/me/settings", json={"tg_notifications_enabled": True}
            )
    assert patched.status_code == 200
    assert patched.json()["tg_notifications_enabled"] is False
    assert fetched.json()["tg_notifications_enabled"] is False
    assert reenabled.json()["tg_notifications_enabled"] is True


async def test_superadmin_without_uid_403() -> None:
    async with mail_db() as sm:
        # Супер-админ из .env не имеет БД-строки (user_id=None) → 403.
        principal = build_principal(user_id=None, is_superadmin=True)
        app = build_app(sm, principal)
        async with client(app) as c:
            get_resp = await c.get("/api/mail/me/settings")
            patch_resp = await c.patch(
                "/api/mail/me/settings", json={"tg_notifications_enabled": False}
            )
    assert get_resp.status_code == 403
    assert patch_resp.status_code == 403


async def test_view_permission_required() -> None:
    async with mail_db() as sm:
        async with sm() as s:
            role = await seed_role(s, permissions={"mail": []})
            user = await seed_user(s, role)
            await s.commit()
            uid = user.id
        principal = build_principal(
            user_id=uid, is_superadmin=False, role="member", permissions={"mail": []}
        )
        app = build_app(sm, principal)
        async with client(app) as c:
            resp = await c.get("/api/mail/me/settings")
    assert resp.status_code == 403
