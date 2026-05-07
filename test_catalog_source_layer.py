#!/usr/bin/env python3
import os
import json
import asyncio

from deps import db
from catalog_service import get_effective_products, get_catalog_source_info


LAPAKIN_SHOP_ID = os.environ.get("LAPAKIN_SHOP_ID", "shop_f448a2bd71da")
STANDALONE_SHOP_ID = os.environ.get("STANDALONE_SHOP_ID", "shop_golden_suite")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def ensure_standalone_seed():
    now = "catalog_source_test"

    await db.shops.update_one(
        {"shop_id": STANDALONE_SHOP_ID},
        {
            "$set": {
                "shop_id": STANDALONE_SHOP_ID,
                "name": "Standalone Catalog Test",
                "source": "standalone",
                "catalog_source": "local",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    if await db.products.count_documents({"shop_id": STANDALONE_SHOP_ID}) == 0:
        await db.products.insert_many([
            {"shop_id": STANDALONE_SHOP_ID, "product_id": "p1", "name": "Produk A", "price": 10000, "status": "active"},
            {"shop_id": STANDALONE_SHOP_ID, "product_id": "p2", "name": "Produk B", "price": 20000, "status": "active"},
            {"shop_id": STANDALONE_SHOP_ID, "product_id": "p3", "name": "Produk C", "price": 30000, "status": "active"},
        ])


async def main():
    await ensure_standalone_seed()

    standalone_products = await get_effective_products(STANDALONE_SHOP_ID)
    standalone_info = await get_catalog_source_info(STANDALONE_SHOP_ID)

    pretty("STANDALONE EFFECTIVE CATALOG", {
        "info": standalone_info,
        "count": len(standalone_products),
        "sample": standalone_products[:3],
    })

    assert standalone_info["catalog_source"] == "local"
    assert len(standalone_products) >= 3

    lapakin_shop = await db.shops.find_one({"shop_id": LAPAKIN_SHOP_ID}, {"_id": 0})
    if not lapakin_shop:
        pretty("LAPAKIN SHOP SKIPPED", {"reason": "shop not found", "shop_id": LAPAKIN_SHOP_ID})
    else:
        lapakin_products = await get_effective_products(LAPAKIN_SHOP_ID)
        lapakin_info = await get_catalog_source_info(LAPAKIN_SHOP_ID)

        pretty("LAPAKIN EFFECTIVE CATALOG", {
            "shop": lapakin_shop,
            "info": lapakin_info,
            "count": len(lapakin_products),
            "sample": lapakin_products[:5],
        })

        assert lapakin_info["catalog_source"] == "lapakin"

        if len(lapakin_products) == 0:
            raise SystemExit(
                "Lapakin shop found but effective products are empty. "
                "Check context_service Lapakin product fetch / lapakin_shop_id."
            )

    print("\nOK catalog source layer test passed")


if __name__ == "__main__":
    asyncio.run(main())
