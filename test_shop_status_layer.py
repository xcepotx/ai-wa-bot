#!/usr/bin/env python3
import os
import json
import asyncio

from deps import db
from shop_status_service import get_effective_shop_status, enrich_context_with_shop_status


LAPAKIN_SHOP_ID = os.environ.get("LAPAKIN_SHOP_ID", "shop_f448a2bd71da")
STANDALONE_SHOP_ID = os.environ.get("STANDALONE_SHOP_ID", "shop_status_standalone_test")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def seed_standalone_status_shop():
    now = "shop_status_test"

    await db.shops.update_one(
        {"shop_id": STANDALONE_SHOP_ID},
        {
            "$set": {
                "shop_id": STANDALONE_SHOP_ID,
                "name": "Standalone Status Test",
                "source": "standalone",
                "timezone": "Asia/Jakarta",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    await db.bot_shop_profile.update_one(
        {"shop_id": STANDALONE_SHOP_ID},
        {
            "$set": {
                "shop_id": STANDALONE_SHOP_ID,
                "business_hours": "Setiap hari 00.00-23.59",
                "timezone": "Asia/Jakarta",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    await db.bot_settings.update_one(
        {"shop_id": STANDALONE_SHOP_ID},
        {
            "$set": {
                "shop_id": STANDALONE_SHOP_ID,
                "enabled": True,
                "mode": "draft_only",
                "business_hours": "Setiap hari 00.00-23.59",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def main():
    await seed_standalone_status_shop()

    standalone = await get_effective_shop_status(STANDALONE_SHOP_ID)
    pretty("STANDALONE EFFECTIVE SHOP STATUS", standalone)

    assert standalone["source"] == "local"
    assert standalone["business_hours_available"] is True
    assert standalone["open_now"] is True

    enriched = await enrich_context_with_shop_status(STANDALONE_SHOP_ID, {})
    pretty("ENRICHED CONTEXT STATUS SAMPLE", {
        "business_hours": enriched.get("business_hours"),
        "open_now": enriched.get("open_now"),
        "open_status": enriched.get("open_status"),
        "shop_status": enriched.get("shop_status"),
    })

    lapakin_shop = await db.shops.find_one({"shop_id": LAPAKIN_SHOP_ID}, {"_id": 0})
    if not lapakin_shop:
        pretty("LAPAKIN STATUS SKIPPED", {"reason": "shop not found", "shop_id": LAPAKIN_SHOP_ID})
    else:
        lapakin = await get_effective_shop_status(LAPAKIN_SHOP_ID)
        pretty("LAPAKIN EFFECTIVE SHOP STATUS", lapakin)

        assert lapakin["source"] == "lapakin"
        if not lapakin["business_hours_available"]:
            raise SystemExit(
                "Lapakin shop found but business_hours is empty. "
                "Check LapakinUMKM context/status adapter."
            )

    print("\nOK shop status layer test passed")


if __name__ == "__main__":
    asyncio.run(main())
