#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "alex.solachuddin@gmail.com")
SHOP_ID = os.environ.get("SHOP_ID")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def http(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=payload, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed


async def main():
    admin = await db.users.find_one({"email": ADMIN_EMAIL}, {"_id": 0})
    if not admin:
        raise SystemExit(f"Admin not found: {ADMIN_EMAIL}")
    if admin.get("role") != "admin":
        raise SystemExit(f"User is not admin: {ADMIN_EMAIL}")

    shop_id = SHOP_ID
    if not shop_id:
        shop = await db.shops.find_one({}, {"_id": 0, "shop_id": 1})
        if not shop:
            raise SystemExit("No shop found")
        shop_id = shop["shop_id"]

    token = create_access_token(admin["user_id"], admin["email"])

    pretty("CONFIG", {
        "admin_email": ADMIN_EMAIL,
        "shop_id": shop_id,
    })

    payload = {
        "provider": "meta_cloud",
        "status": "configured",
        "enabled": False,
        "display_name": "Test Meta Credential",
        "display_phone_number": "+628000000000",
        "phone_number_id": "1234567890",
        "waba_id": "9876543210",
        "business_account_id": "555555",
        "access_token": "EAATEST_ACCESS_TOKEN_SECRET_1234",
        "webhook_secret": "test_webhook_secret",
        "notes": "Created by test_provider_credentials.py"
    }

    status, saved = http("PUT", f"/api/admin/shops/{shop_id}/provider-credentials", token=token, body=payload)
    pretty(f"PUT credentials -> {status}", saved)
    assert status == 200
    assert saved["configured"] is True
    assert saved["access_token_present"] is True
    assert saved["access_token_last4"] == "1234"
    assert "EAATEST" not in json.dumps(saved)

    status, fetched = http("GET", f"/api/admin/shops/{shop_id}/provider-credentials", token=token)
    pretty(f"GET credentials -> {status}", fetched)
    assert status == 200
    assert fetched["access_token_present"] is True
    assert "access_token_encrypted" not in fetched

    status, tested = http("POST", f"/api/admin/shops/{shop_id}/provider-credentials/test", token=token)
    pretty(f"POST test credentials -> {status}", tested)
    assert status == 200
    assert tested["ok"] is True

    status, listed = http("GET", "/api/admin/provider-credentials", token=token)
    pretty(f"GET list credentials -> {status}", listed)
    assert status == 200
    assert "items" in listed

    print("\nOK provider credentials test passed")


if __name__ == "__main__":
    asyncio.run(main())
