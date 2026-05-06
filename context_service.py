"""
Context Service — fetch data toko dari Lapakin API.
Cache simple in-memory 60 detik untuk mengurangi load ke Lapakin.
"""
import os
import time
import logging
import httpx

logger = logging.getLogger("ai-wa-bot")

_cache: dict = {}
_CACHE_TTL = 60  # detik


async def get_shop_context(shop_id: str) -> dict | None:
    """Fetch context toko dari Lapakin /api/bot/shops/{shop_id}/context"""
    now = time.time()

    # Return cache kalau masih fresh
    cached = _cache.get(shop_id)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    lapakin_url = os.environ.get("LAPAKIN_API_URL", "https://dev.lapakin.my.id")
    bot_token   = os.environ.get("BOT_SERVICE_TOKEN", "")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{lapakin_url}/api/bot/shops/{shop_id}/context",
                headers={"X-Bot-Token": bot_token},
            )
        if r.status_code != 200:
            logger.warning("Lapakin context fetch failed: %s %s", shop_id, r.status_code)
            return None

        data = r.json()
        _cache[shop_id] = {"data": data, "ts": now}
        return data

    except Exception as e:
        logger.error("context_service error: %s", e)
        return None


async def get_context_by_phone(phone: str) -> dict | None:
    """Fetch context berdasarkan nomor WA."""
    lapakin_url = os.environ.get("LAPAKIN_API_URL", "https://dev.lapakin.my.id")
    bot_token   = os.environ.get("BOT_SERVICE_TOKEN", "")
    clean       = phone.replace("whatsapp:", "").replace("+", "").replace(" ", "").strip()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{lapakin_url}/api/bot/shops/by-wa/{clean}/context",
                headers={"X-Bot-Token": bot_token},
            )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        logger.error("context_by_phone error: %s", e)
        return None


def invalidate_cache(shop_id: str):
    """Hapus cache toko — dipanggil setelah ada update settings."""
    _cache.pop(shop_id, None)
