#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.parse
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
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(data)


def http(method, path, token=None, body=None, raw=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=payload, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw_body = res.read().decode("utf-8")
            if raw:
                return res.status, raw_body
            return res.status, json.loads(raw_body) if raw_body else {}
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw_body)
        except Exception:
            parsed = raw_body
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

    verify_token = "verify_meta_test_lapakin_asisten_123"
    challenge = "CHALLENGE_987654321"

    await db.provider_credentials.update_one(
        {"shop_id": shop_id},
        {
            "$set": {
                "shop_id": shop_id,
                "provider": "meta_cloud",
                "status": "configured",
                "enabled": False,
                "verify_token": verify_token,
                "updated_at": "test",
            },
            "$setOnInsert": {
                "credential_id": "cred_meta_test",
                "created_at": "test",
            },
        },
        upsert=True,
    )

    pretty("CONFIG", {
        "base_url": BASE_URL,
        "shop_id": shop_id,
        "verify_token": verify_token,
        "challenge": challenge,
    })

    query = urllib.parse.urlencode({
        "hub.mode": "subscribe",
        "hub.verify_token": verify_token,
        "hub.challenge": challenge,
    })

    status, body = http("GET", f"/api/provider/meta/webhook?{query}", raw=True)
    pretty(f"GET verify good -> {status}", body)
    assert status == 200
    assert body == challenge

    bad_query = urllib.parse.urlencode({
        "hub.mode": "subscribe",
        "hub.verify_token": "wrong_token",
        "hub.challenge": challenge,
    })

    status, bad = http("GET", f"/api/provider/meta/webhook?{bad_query}")
    pretty(f"GET verify bad -> {status}", bad)
    assert status == 403

    status, helper = http("POST", "/api/admin/provider/meta/webhook-test", token=token, body={
        "verify_token": verify_token,
        "challenge": "ADMIN_HELPER_CHALLENGE",
    })
    pretty(f"POST admin helper -> {status}", helper)
    assert status == 200
    assert helper["ok"] is True
    assert helper["challenge"] == "ADMIN_HELPER_CHALLENGE"

    status, placeholder = http("POST", "/api/provider/meta/webhook", body={
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba_test",
                "changes": []
            }
        ]
    })
    pretty(f"POST placeholder -> {status}", placeholder)
    assert status == 200
    assert placeholder["ok"] is True

    print("\nOK meta webhook verification test passed")


if __name__ == "__main__":
    asyncio.run(main())
