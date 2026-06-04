"""Owner SpaceCraft product sync routes."""
import os

from fastapi import APIRouter, HTTPException, Query, Request

from deps import db, require_user, now_iso, new_id
from services.spacecraft_product_sync import sync_spacecraft_products


router = APIRouter()


def _expected_spacecraft_shop_id() -> str:
    return os.environ.get("SPACECRAFT_WABOT_SHOP_ID", "spacecraft-main").strip() or "spacecraft-main"


async def _require_spacecraft_owner(request: Request) -> tuple[dict, str]:
    user = await require_user(request)
    shop_id = user.get("shop_id")

    if not shop_id:
        raise HTTPException(status_code=404, detail="Belum punya toko")

    expected = _expected_spacecraft_shop_id()
    if shop_id != expected and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Sync SpaceCraft hanya tersedia untuk toko SpaceCraft")

    return user, expected


@router.get("/spacecraft/sync-status")
async def owner_spacecraft_sync_status(request: Request):
    _, shop_id = await _require_spacecraft_owner(request)

    last_success = await db.bot_events.find_one(
        {"type": "spacecraft.products_synced", "shop_id": shop_id},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    last_failure = await db.bot_events.find_one(
        {"type": "spacecraft.products_sync_failed", "shop_id": shop_id},
        {"_id": 0},
        sort=[("created_at", -1)],
    )

    product_count = await db.products.count_documents({
        "shop_id": shop_id,
        "source": "spacecraft_api",
        "status": "active",
    })

    return {
        "ok": True,
        "shop_id": shop_id,
        "auto_sync_enabled": os.environ.get("SPACECRAFT_AUTO_SYNC_ENABLED", "true"),
        "auto_sync_seconds": int(os.environ.get("SPACECRAFT_AUTO_SYNC_SECONDS", "1800")),
        "active_product_count": product_count,
        "last_success": last_success,
        "last_failure": last_failure,
    }


@router.post("/spacecraft/sync-products")
async def owner_spacecraft_sync_products(request: Request):
    user, shop_id = await _require_spacecraft_owner(request)

    result = await sync_spacecraft_products()

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "owner.spacecraft.products_sync_triggered",
        "payload": {
            "user_id": user.get("user_id"),
            "email": user.get("email"),
            "result": result,
        },
        "created_at": now_iso(),
    })

    return result


@router.get("/spacecraft/products")
async def owner_spacecraft_products(
    request: Request,
    status: str = Query("active"),
    price_filter: str = Query("all"),
    q: str = Query(""),
    limit: int = Query(100, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    _, shop_id = await _require_spacecraft_owner(request)

    base_query = {
        "shop_id": shop_id,
        "source": "spacecraft_api",
    }

    if status and status != "all":
        base_query["status"] = status

    conditions = []

    query_text = (q or "").strip()
    if query_text:
        conditions.append({
            "$or": [
                {"name": {"$regex": query_text, "$options": "i"}},
                {"name_en": {"$regex": query_text, "$options": "i"}},
                {"slug": {"$regex": query_text, "$options": "i"}},
                {"sku": {"$regex": query_text, "$options": "i"}},
                {"category": {"$regex": query_text, "$options": "i"}},
                {"category_name": {"$regex": query_text, "$options": "i"}},
                {"description": {"$regex": query_text, "$options": "i"}},
                {"search_keywords": {"$regex": query_text, "$options": "i"}},
            ]
        })

    if price_filter == "numeric_price":
        conditions.append({"price": {"$gt": 0}})
    elif price_filter == "quote_price":
        conditions.append({
            "$or": [
                {"price": None},
                {"price": {"$exists": False}},
                {"price": {"$lte": 0}},
                {"price_label": {"$regex": "konfirmasi|quote|hubungi|admin", "$options": "i"}},
            ]
        })
    elif price_filter == "no_image":
        conditions.append({
            "$or": [
                {"image_url": None},
                {"image_url": ""},
                {"image_url": {"$exists": False}},
            ]
        })

    query = dict(base_query)
    if conditions:
        query["$and"] = conditions

    projection = {
        "_id": 0,
        "product_id": 1,
        "external_id": 1,
        "sku": 1,
        "name": 1,
        "name_en": 1,
        "slug": 1,
        "category": 1,
        "category_name": 1,
        "short_description": 1,
        "product_type": 1,
        "minimum_order_quantity": 1,
        "price_mode": 1,
        "price": 1,
        "price_label": 1,
        "base_price": 1,
        "min_price": 1,
        "status": 1,
        "is_active": 1,
        "is_available": 1,
        "is_whatsapp_enabled": 1,
        "image_url": 1,
        "product_url": 1,
        "spacecraft_updated_at": 1,
        "updated_at": 1,
    }

    total = await db.products.count_documents(query)
    items = await db.products.find(query, projection) \
        .sort("updated_at", -1) \
        .skip(skip) \
        .limit(limit) \
        .to_list(limit)

    summary_base = {
        "shop_id": shop_id,
        "source": "spacecraft_api",
    }

    total_products = await db.products.count_documents(summary_base)
    active_products = await db.products.count_documents({**summary_base, "status": "active"})
    inactive_products = await db.products.count_documents({**summary_base, "status": "inactive"})
    numeric_price_products = await db.products.count_documents({**summary_base, "price": {"$gt": 0}})
    no_image_products = await db.products.count_documents({
        **summary_base,
        "$or": [
            {"image_url": None},
            {"image_url": ""},
            {"image_url": {"$exists": False}},
        ],
    })
    quote_price_products = await db.products.count_documents({
        **summary_base,
        "$or": [
            {"price": None},
            {"price": {"$exists": False}},
            {"price": {"$lte": 0}},
        ],
    })

    return {
        "ok": True,
        "items": items,
        "total": total,
        "limit": limit,
        "skip": skip,
        "summary": {
            "total_products": total_products,
            "active_products": active_products,
            "inactive_products": inactive_products,
            "numeric_price_products": numeric_price_products,
            "quote_price_products": quote_price_products,
            "no_image_products": no_image_products,
        },
    }


@router.get("/spacecraft/sync-history")
async def owner_spacecraft_sync_history(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
):
    _, shop_id = await _require_spacecraft_owner(request)

    items = await db.bot_events.find(
        {
            "shop_id": shop_id,
            "type": {
                "$in": [
                    "spacecraft.products_synced",
                    "spacecraft.products_sync_failed",
                    "owner.spacecraft.products_sync_triggered",
                ]
            },
        },
        {"_id": 0},
    ).sort("created_at", -1).limit(limit).to_list(limit)

    return {
        "ok": True,
        "items": items,
        "total": len(items),
    }

