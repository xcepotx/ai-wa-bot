#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token, new_id, now_iso

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "alex.solachuddin@gmail.com")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def http(method, path, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(BASE_URL + path, headers=headers, method=method)

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

    shop = await db.shops.find_one({}, {"_id": 0, "shop_id": 1, "name": 1})
    shop_id = shop["shop_id"] if shop else None

    existing = await db.bot_events.count_documents({})
    if existing == 0:
        await db.bot_events.insert_one({
            "event_id": new_id("evt"),
            "shop_id": shop_id,
            "type": "test.event_seed",
            "payload": {
                "reason": "Seed event for admin events test",
                "code": "TEST_EVENT",
            },
            "created_at": now_iso(),
        })

    token = create_access_token(admin["user_id"], admin["email"])

    pretty("CONFIG", {
        "admin_email": admin["email"],
        "shop_id": shop_id,
    })

    status, data = http("GET", "/api/admin/events", token=token)
    pretty(f"GET /api/admin/events -> {status}", data)
    assert status == 200
    assert "items" in data
    assert "event_types" in data

    first = (data.get("items") or [None])[0]
    if first:
        event_id = first["event_id"]
        status, detail = http("GET", f"/api/admin/events/{event_id}", token=token)
        pretty(f"GET /api/admin/events/{event_id} -> {status}", detail)
        assert status == 200
        assert detail["event"]["event_id"] == event_id

    status, filtered = http("GET", "/api/admin/events?limit=5&q=policy", token=token)
    pretty(f"GET /api/admin/events?q=policy -> {status}", filtered)
    assert status == 200

    print("\nOK admin events test passed")


if __name__ == "__main__":
    asyncio.run(main())
