import asyncio, json, imaplib, ssl
import httpx
from app.config import get_settings

EMAIL = "martenkasparilves01@icloud.com"
BAD = "intentionally-wrong-app-password"

s = get_settings()
url = f"{s.mail_api_base.rstrip('/')}/api/external/mailboxes/test"
body = {
    "email": EMAIL,
    "password": BAD,
    "imap_host": "imap.mail.me.com",
    "imap_port": 993,
    "imap_ssl": True,
    "smtp_host": "smtp.mail.me.com",
    "smtp_port": 587,
    "smtp_ssl": False,
    "smtp_starttls": True,
    "smtp_username": EMAIL,
}

async def probe_agg():
    async with httpx.AsyncClient(timeout=60.0, verify=True) as client:
        r = await client.post(url, json=body, headers={"X-API-Key": s.mail_api_key})
    print("AGG_STATUS", r.status_code)
    try:
        payload = r.json()
    except Exception:
        print("AGG_BODY_RAW", (r.text or "")[:500])
        return
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        safe = {k: err.get(k) for k in ("code", "message", "details", "hint") if k in err}
        print("AGG_ERROR", json.dumps(safe, ensure_ascii=False)[:800])
    else:
        print("AGG_JSON_KEYS", list(payload) if isinstance(payload, dict) else type(payload))

asyncio.run(probe_agg())

try:
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL("imap.mail.me.com", 993, ssl_context=ctx)
    try:
        M.login(EMAIL, BAD)
        print("IMAP_LOGIN_UNEXPECTED_OK")
    except imaplib.IMAP4.error as e:
        print("IMAP_LOGIN_FAIL", type(e).__name__, str(e)[:300])
    finally:
        try:
            M.logout()
        except Exception:
            pass
except Exception as e:
    print("IMAP_CONNECT_FAIL", type(e).__name__, str(e)[:300])
