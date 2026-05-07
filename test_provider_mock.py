#!/usr/bin/env python3
import os
import json
import uuid
import asyncio
import urllib.request
import urllib.error

from deps import db
from test_golden_suite import seed_golden_shop, reset_safety, SHOP_ID


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")


def pretty(title, data):
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def http(method, path, body=None):
    headers = {"Content-Type": "application/json"}
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

    customer_phone = "+6285550001111"
    msg1_id = "mock_test_" + uuid.uuid4().hex[:12]

    status, first = http("POST", "/api/provider/mock/webhook", {
        "shop_id": SHOP_ID,
        "customer_phone": customer_phone,
        "customer_name": "Mock Budi",
        "message": "Harga Nasi Goreng berapa?",
        "provider_message_id": msg1_id,
    })
    pretty("MOCK WEBHOOK #1 PRICE", first)
    assert status == 200
    assert first["ok"] is True
    assert first["provider"] == "mock"
    assert first["provider_status"] == "sent_mock"
    assert first["policy"]["allowed"] is True
    assert "Nasi Goreng" in first["bot_reply"]

    # Idempotency check.
    status, duplicate = http("POST", "/api/provider/mock/webhook", {
        "shop_id": SHOP_ID,
        "customer_phone": customer_phone,
        "customer_name": "Mock Budi",
        "message": "Harga Nasi Goreng berapa?",
        "provider_message_id": msg1_id,
    })
    pretty("MOCK WEBHOOK DUPLICATE", duplicate)
    assert status == 200
    assert duplicate["duplicate"] is True

    status, second = http("POST", "/api/provider/mock/webhook", {
        "shop_id": SHOP_ID,
        "customer_phone": customer_phone,
        "customer_name": "Mock Budi",
        "message": "Kalau 2 berapa?",
        "provider_message_id": "mock_test_" + uuid.uuid4().hex[:12],
    })
    pretty("MOCK WEBHOOK #2 FOLLOW-UP", second)
    assert status == 200
    assert second["session_id"] == first["session_id"]
    assert second["provider_status"] == "sent_mock"
    assert "Rp30.000" in second["bot_reply"]

    status, stock = http("POST", "/api/provider/mock/webhook", {
        "shop_id": SHOP_ID,
        "customer_phone": "+6285550001212",
        "customer_name": "Mock Stock",
        "message": "Nasi Goreng ada?",
        "provider_message_id": "mock_test_" + uuid.uuid4().hex[:12],
    })
    pretty("MOCK WEBHOOK STOCK HANDOFF", stock)
    assert status == 200
    assert stock["provider_status"] == "handoff"
    assert stock["policy"]["allowed"] is False
    assert stock["policy"]["action"] == "handoff_required"

    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {"$set": {"mode": "draft_only"}},
    )

    status, draft = http("POST", "/api/provider/mock/webhook", {
        "shop_id": SHOP_ID,
        "customer_phone": "+6285550002222",
        "customer_name": "Mock Draft",
        "message": "Produk apa aja?",
        "provider_message_id": "mock_test_" + uuid.uuid4().hex[:12],
    })
    pretty("MOCK WEBHOOK DRAFT MODE", draft)
    assert status == 200
    assert draft["provider_status"] == "draft_only"
    assert draft["policy"]["allowed"] is False
    assert draft["policy"]["action"] == "draft_only"

    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {"$set": {"mode": "auto_reply"}},
    )

    pretty("SUMMARY", {
        "ok": True,
        "shop_id": SHOP_ID,
        "session_id": first["session_id"],
    })

    print("\nOK provider mock test passed")


if __name__ == "__main__":
    asyncio.run(main())
