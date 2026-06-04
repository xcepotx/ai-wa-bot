"""Sync product catalog from SpaceCraft internal API into Wabot Mongo products."""
import os
from typing import Any, Dict, List, Optional

import httpx

from deps import db, now_iso, new_id


def _clean_text(value: Any, limit: int = 4000) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _safe_price(product: Dict[str, Any]) -> Optional[float]:
    raw = product.get("price")
    try:
        if raw is None:
            return None
        value = float(raw)
    except Exception:
        return None

    if value <= 0:
        return None
    return value


def _safe_price_label(product: Dict[str, Any], price: Optional[float]) -> str:
    label = (product.get("price_label") or "").strip()

    if price is None:
        return "Harga perlu dikonfirmasi admin"

    if not label or label in {"Rp 0", "Rp0", "0"}:
        return f"Rp {int(price):,}".replace(",", ".")

    return label


def _map_product(product: Dict[str, Any], shop_id: str) -> Dict[str, Any]:
    source_status = product.get("status")
    is_whatsapp_enabled = bool(product.get("is_whatsapp_enabled", True))
    is_active = bool(product.get("is_active", source_status == "published")) and is_whatsapp_enabled
    price = _safe_price(product)

    name = _clean_text(product.get("name"), 240) or "Produk SpaceCraft"
    category = product.get("category") or product.get("category_name") or "spacecraft"

    description_parts = [
        product.get("short_description"),
        product.get("description"),
        product.get("material"),
        product.get("dimensions"),
        product.get("production_time"),
        product.get("price_note"),
        product.get("search_keywords"),
    ]
    description = _clean_text(" ".join(str(x).strip() for x in description_parts if x), 5000)

    mapped = {
        "shop_id": shop_id,
        "product_id": product.get("product_id") or f"spacecraft-product-{product.get('external_id')}",
        "external_id": str(product.get("external_id") or ""),
        "source": "spacecraft_api",
        "source_shop_id": "spacecraft-main",
        "source_status": source_status,
        "name": name,
        "name_en": product.get("name_en"),
        "slug": product.get("slug"),
        "sku": product.get("sku"),
        "category": category,
        "category_name": product.get("category_name"),
        "description": description,
        "short_description": _clean_text(product.get("short_description"), 1200),
        "material": product.get("material"),
        "dimensions": product.get("dimensions"),
        "production_time": product.get("production_time"),
        "product_type": product.get("product_type"),
        "minimum_order_quantity": product.get("minimum_order_quantity"),
        "price_mode": product.get("price_mode"),
        "price": price,
        "price_label": _safe_price_label(product, price),
        "base_price": product.get("base_price"),
        "min_price": product.get("min_price"),
        "compare_at_price": product.get("compare_at_price"),
        "price_note": product.get("price_note"),
        "status": "active" if is_active else "inactive",
        "is_active": is_active,
        "is_available": bool(product.get("is_available", is_active)) and is_active,
        "is_recommended": bool(product.get("is_featured") or product.get("is_best_seller") or product.get("is_new")),
        "is_whatsapp_enabled": is_whatsapp_enabled,
        "image_url": product.get("image_url"),
        "product_url": product.get("product_url"),
        "search_keywords": product.get("search_keywords"),
        "spacecraft_updated_at": product.get("updated_at"),
        "updated_at": now_iso(),
    }

    return mapped


async def sync_spacecraft_products() -> Dict[str, Any]:
    feed_url = os.environ.get("SPACECRAFT_PRODUCT_FEED_URL", "").strip()
    sync_key = os.environ.get("SPACECRAFT_WABOT_SYNC_KEY", "").strip()
    shop_id = os.environ.get("SPACECRAFT_WABOT_SHOP_ID", "spacecraft-main").strip() or "spacecraft-main"

    if not feed_url:
        raise RuntimeError("SPACECRAFT_PRODUCT_FEED_URL belum diset.")
    if not sync_key:
        raise RuntimeError("SPACECRAFT_WABOT_SYNC_KEY belum diset.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            feed_url,
            headers={
                "X-SpaceCraft-Wabot-Key": sync_key,
                "Accept": "application/json",
                "User-Agent": "Wabot-SpaceCraft-Sync/1.0",
            },
        )

    response.raise_for_status()
    payload = response.json()

    if not payload.get("ok"):
        raise RuntimeError(f"SpaceCraft feed returned not ok: {payload}")

    products: List[Dict[str, Any]] = payload.get("products") or []

    seen_product_ids = []
    upserted = 0
    modified = 0
    skipped = 0

    for raw_product in products:
        mapped = _map_product(raw_product, shop_id)
        product_id = mapped.get("product_id")

        if not product_id:
            skipped += 1
            continue

        seen_product_ids.append(product_id)

        result = await db.products.update_one(
            {"product_id": product_id},
            {
                "$set": mapped,
                "$setOnInsert": {
                    "created_at": now_iso(),
                },
            },
            upsert=True,
        )

        if result.upserted_id is not None:
            upserted += 1
        elif result.modified_count:
            modified += 1

    deactivated = 0
    if seen_product_ids:
        deact_result = await db.products.update_many(
            {
                "shop_id": shop_id,
                "source": "spacecraft_api",
                "product_id": {"$nin": seen_product_ids},
            },
            {
                "$set": {
                    "status": "inactive",
                    "is_active": False,
                    "is_available": False,
                    "updated_at": now_iso(),
                    "deactivated_reason": "missing_from_spacecraft_feed",
                }
            },
        )
        deactivated = deact_result.modified_count

    summary = {
        "ok": True,
        "shop_id": shop_id,
        "feed_url": feed_url,
        "spacecraft_count": len(products),
        "seen": len(seen_product_ids),
        "upserted": upserted,
        "modified": modified,
        "skipped": skipped,
        "deactivated": deactivated,
        "synced_at": now_iso(),
    }

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "spacecraft.products_synced",
        "payload": summary,
        "created_at": now_iso(),
    })

    return summary
