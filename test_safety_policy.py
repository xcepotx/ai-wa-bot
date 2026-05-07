#!/usr/bin/env python3
import os
import json
import asyncio

from deps import db
from safety_policy import evaluate_auto_reply_policy

SHOP_ID = os.environ.get("SHOP_ID")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def pick_shop_id():
    if SHOP_ID:
        return SHOP_ID

    shop = await db.shops.find_one({}, {"_id": 0, "shop_id": 1, "name": 1})
    if not shop:
        raise SystemExit("No shop found")
    return shop["shop_id"]


async def main():
    shop_id = await pick_shop_id()

    pretty("CONFIG", {"shop_id": shop_id})

    # Reset global to ON and shop to enabled auto_reply for policy happy-path.
    await db.system_settings.update_one(
        {"key": "lapakin_asisten_control"},
        {"$set": {"key": "lapakin_asisten_control", "status": "on", "reason": ""}},
        upsert=True,
    )

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {
            "$set": {
                "shop_id": shop_id,
                "enabled": True,
                "mode": "auto_reply",
                "admin_disabled": False,
                "quota_monthly": 100,
                "quota_used": 0,
            }
        },
        upsert=True,
    )

    ok = await evaluate_auto_reply_policy(
        shop_id=shop_id,
        reply_result={
            "intent": "price_inquiry",
            "confidence": "high",
            "handoff_required": False,
        },
        channel="whatsapp",
        require_provider_ready=False,
    )
    pretty("ALLOW CASE", ok)
    assert ok["allowed"] is True
    assert ok["action"] == "send_auto_reply"

    draft = None
    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {"$set": {"mode": "draft_only"}},
    )
    draft = await evaluate_auto_reply_policy(
        shop_id=shop_id,
        reply_result={
            "intent": "price_inquiry",
            "confidence": "high",
            "handoff_required": False,
        },
        channel="whatsapp",
        require_provider_ready=False,
    )
    pretty("DRAFT MODE CASE", draft)
    assert draft["allowed"] is False
    assert draft["action"] == "draft_only"

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {"$set": {"mode": "auto_reply"}},
    )

    handoff = await evaluate_auto_reply_policy(
        shop_id=shop_id,
        reply_result={
            "intent": "complaint",
            "confidence": "high",
            "handoff_required": False,
        },
        channel="whatsapp",
        require_provider_ready=False,
    )
    pretty("SENSITIVE INTENT CASE", handoff)
    assert handoff["allowed"] is False
    assert handoff["action"] == "handoff_required"

    await db.system_settings.update_one(
        {"key": "lapakin_asisten_control"},
        {"$set": {"status": "maintenance", "reason": "test policy maintenance"}},
        upsert=True,
    )

    global_block = await evaluate_auto_reply_policy(
        shop_id=shop_id,
        reply_result={
            "intent": "price_inquiry",
            "confidence": "high",
            "handoff_required": False,
        },
        channel="whatsapp",
        require_provider_ready=False,
    )
    pretty("GLOBAL BLOCK CASE", global_block)
    assert global_block["allowed"] is False
    assert global_block["action"] == "skipped_global_disabled"

    # Restore ON for dev.
    await db.system_settings.update_one(
        {"key": "lapakin_asisten_control"},
        {"$set": {"status": "on", "reason": ""}},
        upsert=True,
    )

    print("\nOK auto-reply safety policy test passed")


if __name__ == "__main__":
    asyncio.run(main())
