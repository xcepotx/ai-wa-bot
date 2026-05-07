"""
Context Service — fetch data toko untuk AI engine.

Dua mode:
  standalone → fetch dari DB ai-wa-bot sendiri
  lapakin    → fetch dari Lapakin API (integrasi nanti)
"""
import os
import time
import logging
import httpx

from deps import db

logger  = logging.getLogger("ai-wa-bot")
_cache: dict = {}
_CACHE_TTL   = 60  # detik


async def get_shop_context(shop_id: str) -> dict | None:
    """Entry point utama — auto-detect source dari DB."""
    now    = time.time()
    cached = _cache.get(shop_id)
    if cached and (now - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        return None

    source = shop.get("source", "standalone")

    if source == "lapakin":
        # Pakai lapakin_shop_id untuk fetch ke Lapakin API
        lapakin_shop_id = shop.get("lapakin_shop_id", shop_id)
        data = await _fetch_from_lapakin(lapakin_shop_id)
    else:
        data = await _fetch_from_local_db(shop, shop_id)

    if data:
        _cache[shop_id] = {"data": data, "ts": now}

    return data


async def _fetch_from_local_db(shop: dict, shop_id: str) -> dict:
    """Fetch context dari DB ai-wa-bot sendiri (standalone mode)."""
    products = await db.products.find(
        {"shop_id": shop_id, "is_active": True},
        {"_id": 0, "product_id": 1, "name": 1, "price": 1,
         "stock": 1, "description": 1, "is_available": 1,
         "promo_label": 1, "is_recommended": 1}
    ).to_list(200)

    payment     = await db.payment_info.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    bot_settings = await db.bot_settings.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    faqs        = await db.bot_faqs.find(
        {"shop_id": shop_id, "is_active": True}, {"_id": 0}
    ).sort("category", 1).to_list(200)
    bot_profile = await db.bot_shop_profile.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    owner       = await db.users.find_one(
        {"user_id": shop.get("owner_user_id")},
        {"_id": 0, "password_hash": 0}
    ) or {}

    return {
        "shop": {
            "shop_id":       shop_id,
            "name":          shop.get("name"),
            "description":   shop.get("description"),
            "whatsapp":      shop.get("whatsapp"),
            "category":      shop.get("business_type"),
            "address":       shop.get("address"),
            "hours":         shop.get("hours"),
            "about":         shop.get("about"),
            "is_active":     shop.get("is_active", True),
            "order_methods": bot_profile.get("order_methods", []),
            "service_area":  bot_profile.get("service_area"),
            "min_order":     bot_profile.get("min_order"),
            "preorder_policy": bot_profile.get("preorder_policy"),
            "store_notes":   bot_profile.get("store_notes"),
            "promo_active":  False,
        },
        "payment": {
            "instruction":   payment.get("instruction"),
            "qris_image":    payment.get("qris_image_url"),
            "qris_available": payment.get("qris_available", False),
            "bank_accounts": payment.get("bank_accounts", []),
            "payment_notes": payment.get("payment_notes"),
            "cod_available": payment.get("cod_available", False),
        },
        "products": products,
        "faqs":     faqs,
        "bot_settings": {
            "enabled":       bot_settings.get("enabled", False),
            "mode":          bot_settings.get("mode", "off"),
            "tone":          bot_settings.get("tone", "ramah"),
            "bot_name":      bot_settings.get("bot_name", "Admin"),
            "language":      bot_settings.get("language", "id"),
            "outside_hours_message": bot_settings.get(
                "outside_hours_message",
                "Halo kak! Saat ini kami sedang tutup 🙏"
            ),
            "fallback_message": bot_settings.get(
                "fallback_message",
                "Maaf kak, silakan hubungi admin kami ya 🙏"
            ),
            "handoff_keywords": bot_settings.get(
                "handoff_keywords",
                ["komplain", "refund", "batal", "bicara admin"]
            ),
            "max_auto_replies": bot_settings.get("max_auto_replies", 10),
        },
        "owner": {
            "user_id": owner.get("user_id"),
            "name":    owner.get("name"),
            "plan":    owner.get("plan", "free"),
        },
        "source": "standalone",
    }


async def _fetch_from_lapakin(shop_id: str) -> dict | None:
    """Fetch context dari Lapakin API (untuk user yang connect Lapakin)."""
    lapakin_url = os.environ.get("LAPAKIN_API_URL", "")
    bot_token   = os.environ.get("BOT_SERVICE_TOKEN", "")

    if not lapakin_url or not bot_token:
        logger.warning("Lapakin integration tidak dikonfigurasi")
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{lapakin_url}/api/bot/shops/{shop_id}/context",
                headers={"X-Bot-Token": bot_token},
            )
        if r.status_code != 200:
            logger.warning("Lapakin context fetch failed: %s", r.status_code)
            return None
        data = r.json()
        data["source"] = "lapakin"
        return data
    except Exception as e:
        logger.error("Lapakin fetch error: %s", e)
        return None


def invalidate_cache(shop_id: str):
    _cache.pop(shop_id, None)
