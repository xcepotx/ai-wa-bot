#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db
from context_service import get_shop_context
from reply_rules import _extract_products


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
SHOP_ID = os.environ.get("SHOP_ID")


def http(method, path, body=None):
    url = BASE_URL + path
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(body).encode("utf-8") if body is not None else None

    req = urllib.request.Request(url, data=payload, headers=headers, method=method)

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


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


async def pick_shop_id():
    if SHOP_ID:
        return SHOP_ID

    user = await db.users.find_one({"shop_id": {"$exists": True, "$ne": None}}, {"_id": 0})
    if not user:
        raise SystemExit("No user with shop_id found. Set SHOP_ID=...")
    return user["shop_id"]


async def main():
    shop_id = await pick_shop_id()
    ctx = await get_shop_context(shop_id)
    products = _extract_products(ctx)

    products = [p for p in products if p.get("name")]
    if not products:
        raise SystemExit(f"No products found for shop_id={shop_id}")

    product = products[0]
    product_name = product["name"]

    pretty("CONFIG", {
        "shop_id": shop_id,
        "product_name": product_name,
        "product_price": product.get("price"),
    })

    status, sim1 = http("POST", "/api/simulate", {
        "shop_id": shop_id,
        "customer_name": "Budi",
        "customer_phone": "+6282222222222",
        "customer_message": f"{product_name} ada?"
    })
    pretty(f"SIM 1 -> {status}", sim1)
    if status != 200:
        raise SystemExit("SIM 1 failed")

    session_id = sim1["session_id"]

    status, sim2 = http("POST", "/api/simulate", {
        "shop_id": shop_id,
        "session_id": session_id,
        "customer_name": "Budi",
        "customer_phone": "+6282222222222",
        "customer_message": "Kalau 2 berapa?"
    })
    pretty(f"SIM 2 -> {status}", sim2)
    if status != 200:
        raise SystemExit("SIM 2 failed")

    ok_source = sim2.get("source") in {"rule_product_memory", "rule_product"}
    ok_mentions_product = product_name.lower() in (sim2.get("bot_reply") or "").lower()

    pretty("ASSERT", {
        "session_id": session_id,
        "source_is_rule": ok_source,
        "reply_mentions_product": ok_mentions_product,
        "reply": sim2.get("bot_reply"),
    })

    if not ok_source:
        raise SystemExit("Expected SIM 2 to use rule_product_memory or rule_product")

    print("\nOK product memory test passed")


if __name__ == "__main__":
    asyncio.run(main())
