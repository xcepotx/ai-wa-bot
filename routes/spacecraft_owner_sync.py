from datetime import datetime, timedelta, timezone
"""Owner SpaceCraft product sync routes."""
import os

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from deps import db, require_user, now_iso, new_id
from services.spacecraft_product_sync import sync_spacecraft_products


router = APIRouter()


class SpaceCraftProductIntelligenceIn(BaseModel):
    bot_aliases: list[str] = []
    bot_keywords: list[str] = []
    bot_notes: str = ""


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




def _cc_clean_doc(doc):
    if not doc:
        return None
    out = {}
    for key, value in dict(doc).items():
        if key == "_id":
            continue
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        elif isinstance(value, dict):
            out[key] = _cc_clean_doc(value)
        elif isinstance(value, list):
            out[key] = [_cc_clean_doc(v) if isinstance(v, dict) else v for v in value]
        else:
            out[key] = value
    return out


def _cc_parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


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
        "bot_aliases": 1,
        "bot_keywords": 1,
        "bot_notes": 1,
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


def _clean_intel_list(values):
    if not isinstance(values, list):
        return []
    out = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        text = text[:80]
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= 30:
            break
    return out


@router.get("/spacecraft/products/{product_id}/intelligence")
async def owner_spacecraft_product_intelligence(product_id: str, request: Request):
    _, shop_id = await _require_spacecraft_owner(request)

    product = await db.products.find_one(
        {
            "shop_id": shop_id,
            "source": "spacecraft_api",
            "product_id": product_id,
        },
        {
            "_id": 0,
            "product_id": 1,
            "name": 1,
            "category": 1,
            "category_name": 1,
            "search_keywords": 1,
            "bot_aliases": 1,
            "bot_keywords": 1,
            "bot_notes": 1,
            "updated_at": 1,
        },
    )

    if not product:
        raise HTTPException(status_code=404, detail="Produk SpaceCraft tidak ditemukan.")

    return {
        "ok": True,
        "product": product,
        "intelligence": {
            "bot_aliases": product.get("bot_aliases") or [],
            "bot_keywords": product.get("bot_keywords") or [],
            "bot_notes": product.get("bot_notes") or "",
        },
    }


@router.put("/spacecraft/products/{product_id}/intelligence")
async def owner_spacecraft_update_product_intelligence(
    product_id: str,
    data: SpaceCraftProductIntelligenceIn,
    request: Request,
):
    user, shop_id = await _require_spacecraft_owner(request)

    update = {
        "bot_aliases": _clean_intel_list(data.bot_aliases),
        "bot_keywords": _clean_intel_list(data.bot_keywords),
        "bot_notes": str(data.bot_notes or "").strip()[:1200],
        "intelligence_updated_at": now_iso(),
        "intelligence_updated_by": user.get("email") or user.get("user_id"),
        "updated_at": now_iso(),
    }

    result = await db.products.update_one(
        {
            "shop_id": shop_id,
            "source": "spacecraft_api",
            "product_id": product_id,
        },
        {"$set": update},
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produk SpaceCraft tidak ditemukan.")

    product = await db.products.find_one(
        {"shop_id": shop_id, "source": "spacecraft_api", "product_id": product_id},
        {"_id": 0},
    )

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "spacecraft.product_intelligence_updated",
        "payload": {
            "product_id": product_id,
            "product_name": product.get("name") if product else None,
            "bot_aliases_count": len(update["bot_aliases"]),
            "bot_keywords_count": len(update["bot_keywords"]),
            "user_id": user.get("user_id"),
            "email": user.get("email"),
        },
        "created_at": now_iso(),
    })

    return {
        "ok": True,
        "product": product,
        "intelligence": {
            "bot_aliases": update["bot_aliases"],
            "bot_keywords": update["bot_keywords"],
            "bot_notes": update["bot_notes"],
        },
    }


@router.get("/spacecraft/command-center")
async def owner_spacecraft_command_center(request: Request):
    _, shop_id = await _require_spacecraft_owner(request)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    since_7d = (now - timedelta(days=7)).isoformat()

    product_base = {"shop_id": shop_id, "source": "spacecraft_api"}
    no_price_query = {
        **product_base,
        "$or": [
            {"price": None},
            {"price": {"$exists": False}},
            {"price": {"$lte": 0}},
        ],
    }
    no_image_query = {
        **product_base,
        "$or": [
            {"image_url": None},
            {"image_url": ""},
            {"image_url": {"$exists": False}},
        ],
    }

    lead_base = {"shop_id": shop_id}
    need_follow_statuses = ["new", "contact_requested", "notified"]

    products_total = await db.products.count_documents(product_base)
    products_active = await db.products.count_documents({**product_base, "status": "active"})
    products_no_price = await db.products.count_documents(no_price_query)
    products_no_image = await db.products.count_documents(no_image_query)

    leads_total = await db.webchat_leads.count_documents(lead_base)
    leads_today = await db.webchat_leads.count_documents({**lead_base, "created_at": {"$gte": today_start}})
    leads_7d = await db.webchat_leads.count_documents({**lead_base, "created_at": {"$gte": since_7d}})
    leads_need_follow_up = await db.webchat_leads.count_documents({
        **lead_base,
        "status": {"$in": need_follow_statuses},
    })
    leads_followed_up = await db.webchat_leads.count_documents({**lead_base, "status": "followed_up"})
    leads_won = await db.webchat_leads.count_documents({**lead_base, "status": "won"})
    leads_lost = await db.webchat_leads.count_documents({**lead_base, "status": "lost"})

    sessions_total = await db.sessions.count_documents({"shop_id": shop_id})
    messages_total = await db.messages.count_documents({"shop_id": shop_id})

    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    bot_settings = await db.bot_settings.find_one({"shop_id": shop_id}, {"_id": 0})

    sync_types = [
        "spacecraft.products_synced",
        "spacecraft.products_sync_failed",
        "owner.spacecraft.products_sync_triggered",
    ]
    latest_sync_event = await db.bot_events.find_one(
        {"shop_id": shop_id, "type": {"$in": sync_types}},
        {"_id": 0},
        sort=[("created_at", -1)],
    )

    latest_leads = await db.webchat_leads.find(
        {"shop_id": shop_id},
        {
            "_id": 0,
            "lead_id": 1,
            "session_id": 1,
            "status": 1,
            "customer_name": 1,
            "customer_phone": 1,
            "need_summary": 1,
            "last_message": 1,
            "intent": 1,
            "confidence": 1,
            "reply_source": 1,
            "created_at": 1,
            "updated_at": 1,
        },
    ).sort("updated_at", -1).limit(6).to_list(6)

    latest_events = await db.bot_events.find(
        {"shop_id": shop_id},
        {"_id": 0, "event_id": 1, "type": 1, "payload": 1, "created_at": 1},
    ).sort("created_at", -1).limit(8).to_list(8)

    latest_conversations = await db.sessions.find(
        {"shop_id": shop_id},
        {
            "_id": 0,
            "session_id": 1,
            "source": 1,
            "customer_name": 1,
            "customer_phone": 1,
            "status": 1,
            "last_message": 1,
            "updated_at": 1,
            "created_at": 1,
        },
    ).sort("updated_at", -1).limit(5).to_list(5)

    warnings = []

    if not bot_settings or not bot_settings.get("enabled"):
        warnings.append({
            "type": "bot_off",
            "level": "high",
            "title": "Bot belum aktif",
            "message": "Aktifkan bot agar pesan pelanggan bisa diproses otomatis.",
            "action_label": "Buka Pengaturan Bot",
            "action_to": "/dashboard/bot",
        })

    if bot_settings and bot_settings.get("mode") != "auto_reply":
        warnings.append({
            "type": "not_auto_reply",
            "level": "medium",
            "title": "Mode bot belum Balas Otomatis",
            "message": f"Mode saat ini: {bot_settings.get('mode') or 'off'}.",
            "action_label": "Buka Pengaturan Bot",
            "action_to": "/dashboard/bot",
        })

    if leads_need_follow_up > 0:
        warnings.append({
            "type": "lead_follow_up",
            "level": "high",
            "title": f"{leads_need_follow_up} lead perlu follow-up",
            "message": "Ada calon pembeli yang sudah menunjukkan minat tetapi belum selesai ditindaklanjuti.",
            "action_label": "Buka Leads",
            "action_to": "/dashboard/webchat-leads",
        })

    if products_no_price > 0:
        warnings.append({
            "type": "product_no_price",
            "level": "medium",
            "title": f"{products_no_price} produk perlu info harga",
            "message": "Produk tanpa harga numeric akan dijawab sebagai harga perlu konfirmasi admin.",
            "action_label": "Cek Produk SpaceCraft",
            "action_to": "/dashboard/spacecraft-products",
        })

    if products_no_image > 0:
        warnings.append({
            "type": "product_no_image",
            "level": "low",
            "title": f"{products_no_image} produk tanpa gambar",
            "message": "Lengkapi gambar agar katalog lebih siap untuk sales assistant.",
            "action_label": "Cek Produk SpaceCraft",
            "action_to": "/dashboard/spacecraft-products",
        })

    last_sync_at = None
    last_sync_age_minutes = None
    if latest_sync_event:
        last_sync_at = latest_sync_event.get("created_at") or latest_sync_event.get("payload", {}).get("synced_at")
        parsed = _cc_parse_dt(last_sync_at)
        if parsed:
            last_sync_age_minutes = round((now - parsed).total_seconds() / 60)

    if not latest_sync_event:
        warnings.append({
            "type": "sync_missing",
            "level": "medium",
            "title": "Produk belum pernah sync",
            "message": "Jalankan sync agar Wabot membaca katalog SpaceCraft terbaru.",
            "action_label": "Sync Produk",
            "action_to": "/dashboard/spacecraft-products",
        })
    elif latest_sync_event.get("type") == "spacecraft.products_sync_failed":
        warnings.append({
            "type": "sync_failed",
            "level": "high",
            "title": "Sync produk terakhir gagal",
            "message": "Cek koneksi SpaceCraft Product Feed API dan key sinkronisasi.",
            "action_label": "Cek Produk SpaceCraft",
            "action_to": "/dashboard/spacecraft-products",
        })
    elif last_sync_age_minutes is not None and last_sync_age_minutes > 90:
        warnings.append({
            "type": "sync_stale",
            "level": "medium",
            "title": "Sync produk sudah lama",
            "message": f"Sync terakhir sekitar {last_sync_age_minutes} menit lalu.",
            "action_label": "Sync Produk",
            "action_to": "/dashboard/spacecraft-products",
        })

    return {
        "ok": True,
        "shop_id": shop_id,
        "shop": _cc_clean_doc(shop),
        "bot": _cc_clean_doc(bot_settings),
        "summary": {
            "products_total": products_total,
            "products_active": products_active,
            "products_no_price": products_no_price,
            "products_no_image": products_no_image,
            "leads_total": leads_total,
            "leads_today": leads_today,
            "leads_7d": leads_7d,
            "leads_need_follow_up": leads_need_follow_up,
            "leads_followed_up": leads_followed_up,
            "leads_won": leads_won,
            "leads_lost": leads_lost,
            "sessions_total": sessions_total,
            "messages_total": messages_total,
            "last_sync_at": last_sync_at,
            "last_sync_age_minutes": last_sync_age_minutes,
            "bot_enabled": bool(bot_settings and bot_settings.get("enabled")),
            "bot_mode": (bot_settings or {}).get("mode") or "off",
        },
        "warnings": warnings,
        "recent": {
            "leads": [_cc_clean_doc(x) for x in latest_leads],
            "events": [_cc_clean_doc(x) for x in latest_events],
            "conversations": [_cc_clean_doc(x) for x in latest_conversations],
        },
        "latest_sync_event": _cc_clean_doc(latest_sync_event),
    }

