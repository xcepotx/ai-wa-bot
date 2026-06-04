"""SpaceCraft product sync endpoint for Wabot."""
import os

from fastapi import APIRouter, Header, HTTPException

from services.spacecraft_product_sync import sync_spacecraft_products

router = APIRouter()


@router.post("/provider/spacecraft/sync-products")
async def provider_spacecraft_sync_products(
    x_wabot_sync_key: str = Header(default="", alias="X-Wabot-Sync-Key"),
):
    expected = os.environ.get("WABOT_INTERNAL_SYNC_KEY", "").strip()

    if not expected:
        raise HTTPException(status_code=503, detail="WABOT_INTERNAL_SYNC_KEY belum diset.")

    if not x_wabot_sync_key or not os.environ.get("WABOT_INTERNAL_SYNC_KEY"):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not __import__("hmac").compare_digest(expected, x_wabot_sync_key):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        return await sync_spacecraft_products()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
