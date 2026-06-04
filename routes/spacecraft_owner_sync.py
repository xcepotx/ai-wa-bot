"""Owner SpaceCraft product sync routes."""
import os

from fastapi import APIRouter, HTTPException, Request

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
