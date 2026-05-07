#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token
from provider_security import encrypt_secret
from test_golden_suite import seed_golden_shop, reset_safety, SHOP_ID

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "alex.solachuddin@gmail.com")


def pretty(title, data):
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def http(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=payload, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed
    except Exception as e:
        return 0, {"error": str(e)}


async def main():
    status, health = http("GET", "/health")
    pretty("Health", health)
    assert status == 200

    admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
    if not admin or admin.get("role") != "admin":
        raise SystemExit(f"Admin user invalid: {ADMIN_EMAIL}")

    admin_token = create_access_token(admin["user_id"], admin["email"])

    await seed_golden_shop()
    await reset_safety()

    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "enabled": True,
                "mode": "auto_reply",
                "admin_disabled": False,
                "quota_monthly": 100,
                "quota_used": 0,
            }
        },
        upsert=True,
    )

    await db.provider_credentials.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "shop_id": SHOP_ID,
                "provider": "meta_cloud",
                "status": "connected",
                "enabled": True,
                "phone_number_id": "meta_phone_golden_001",
                "waba_id": "meta_waba_golden_001",
                "verify_token": "verify_meta_guard_test",
                "access_token_encrypted": encrypt_secret("EAATEST_TOKEN_FOR_GUARD_1234"),
                "access_token_last4": "1234",
                "updated_at": "test",
            },
            "$setOnInsert": {
                "credential_id": "cred_meta_guard_test",
                "created_at": "test",
            },
        },
        upsert=True,
    )

    status, guard = http(
        "GET",
        f"/api/admin/provider/meta/real-send-guard?shop_id={SHOP_ID}",
        token=admin_token,
    )
    pretty(f"GET real-send-guard -> {status}", guard)
    assert status == 200
    assert "allowed" in guard
    assert "checks" in guard

    # In normal dev environment, META_REAL_SEND_ENABLED is usually not true.
    # So guard should block real sends by default.
    server_check = [c for c in guard["checks"] if c["key"] == "server_real_send_enabled"][0]
    pretty("SERVER FLAG CHECK", server_check)

    print("\nOK meta real send guard test passed")


if __name__ == "__main__":
    asyncio.run(main())
