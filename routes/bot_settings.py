"""Bot settings + readiness score routes."""
from fastapi import APIRouter, HTTPException, Request

from deps import db, require_user, now_iso
from models import BotSettingsIn

router = APIRouter()

VALID_MODES = {"off", "simulator_only", "draft_only", "auto_reply"}
VALID_TONES = {"ramah", "santai", "profesional", "singkat", "ceria"}


async def _get_user_shop(user: dict) -> dict:
    shop_id = user.get("shop_id")
    if not shop_id:
        raise HTTPException(status_code=404, detail="Belum punya toko")
    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Toko tidak ditemukan")
    return shop


async def _calculate_readiness(shop_id: str) -> dict:
    shop     = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    products = await db.products.find(
        {"shop_id": shop_id, "is_active": True}, {"_id": 0, "price": 1}
    ).to_list(100)
    payment  = await db.payment_info.find_one({"shop_id": shop_id}) or {}
    faqs     = await db.bot_faqs.find(
        {"shop_id": shop_id, "is_active": True}, {"_id": 0}
    ).to_list(100)
    settings = await db.bot_settings.find_one({"shop_id": shop_id}) or {}

    payment_ok = bool(
        payment.get("instruction")
        or payment.get("qris_available")
        or payment.get("bank_accounts")
    )

    checklist = {
        "nama_toko":        (bool(shop.get("name")),                       10),
        "deskripsi_toko":   (bool(shop.get("description")),                10),
        "whatsapp_ada":     (bool(shop.get("whatsapp")),                   10),
        "produk_minimal_3": (len(products) >= 3,                           15),
        "harga_lengkap":    (all(p.get("price", 0) > 0 for p in products)
                             and len(products) > 0,                        10),
        "jam_buka_ada":     (bool(shop.get("hours")),                      10),
        "payment_ada":      (payment_ok,                                   10),
        "faq_minimal_5":    (len(faqs) >= 5,                               10),
        "handoff_keyword":  (bool(settings.get("handoff_keywords")),       10),
        "fallback_message": (bool(settings.get("fallback_message")),        5),
        "sudah_simulasi":   (bool(settings.get("last_simulated_at")),       5),
    }

    score = sum(w for _, (ok, w) in checklist.items() if ok)

    if score < 50:
        status, label = "not_ready", "Belum Siap"
    elif score < 80:
        status, label = "need_setup", "Perlu Setup"
    else:
        status, label = "ready", "Siap Aktif"

    return {
        "score":          score,
        "status":         status,
        "label":          label,
        "checklist":      {k: {"ok": ok, "points": w}
                           for k, (ok, w) in checklist.items()},
        "can_simulate":   True,
        "can_draft":      score >= 50,
        "can_auto_reply": score >= 80,
    }


@router.get("/bot-settings")
async def get_bot_settings(request: Request):
    user     = await require_user(request)
    shop     = await _get_user_shop(user)
    settings = await db.bot_settings.find_one(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ) or {}
    readiness = await _calculate_readiness(shop["shop_id"])

    return {
        "settings": {
            "shop_id":               shop["shop_id"],
            "enabled":               settings.get("enabled", False),
            "mode":                  settings.get("mode", "off"),
            "tone":                  settings.get("tone", "ramah"),
            "bot_name":              settings.get("bot_name", "Admin"),
            "language":              settings.get("language", "id"),
            "outside_hours_message": settings.get("outside_hours_message", ""),
            "fallback_message":      settings.get("fallback_message", ""),
            "handoff_keywords":      settings.get("handoff_keywords", []),
            "max_auto_replies":      settings.get("max_auto_replies", 10),
            "quota_monthly":         settings.get("quota_monthly", 100),
            "quota_used":            settings.get("quota_used", 0),
            "last_simulated_at":     settings.get("last_simulated_at"),
            "created_at":            settings.get("created_at"),
            "updated_at":            settings.get("updated_at"),
        },
        "readiness": readiness,
    }


@router.put("/bot-settings")
async def update_bot_settings(data: BotSettingsIn, request: Request):
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    shop_id = shop["shop_id"]
    now     = now_iso()

    if data.mode and data.mode not in VALID_MODES:
        raise HTTPException(status_code=400,
                            detail=f"Mode tidak valid. Pilihan: {VALID_MODES}")
    if data.tone and data.tone not in VALID_TONES:
        raise HTTPException(status_code=400,
                            detail=f"Tone tidak valid. Pilihan: {VALID_TONES}")

    if data.mode == "auto_reply":
        readiness = await _calculate_readiness(shop_id)
        if not readiness["can_auto_reply"]:
            raise HTTPException(
                status_code=400,
                detail=f"Readiness score {readiness['score']}/100. Minimal 80 untuk auto-reply."
            )

    update = {"updated_at": now}
    if data.enabled is not None:            update["enabled"]  = data.enabled
    if data.mode is not None:               update["mode"]     = data.mode
    if data.tone is not None:               update["tone"]     = data.tone
    if data.bot_name is not None:           update["bot_name"] = data.bot_name
    if data.language is not None:           update["language"] = data.language
    if data.outside_hours_message is not None:
        update["outside_hours_message"] = data.outside_hours_message
    if data.fallback_message is not None:
        update["fallback_message"] = data.fallback_message
    if data.handoff_keywords is not None:
        update["handoff_keywords"] = data.handoff_keywords
    if data.max_auto_replies is not None:
        update["max_auto_replies"] = max(1, min(data.max_auto_replies, 50))

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {"$set": update, "$setOnInsert": {
            "shop_id":       shop_id,
            "quota_monthly": 100,
            "quota_used":    0,
            "created_at":    now,
        }},
        upsert=True,
    )
    return {"ok": True, "updated": list(update.keys())}


@router.post("/bot-settings/simulate-ping")
async def simulate_ping(request: Request):
    """Tandai owner sudah mencoba simulator."""
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    shop_id = shop["shop_id"]
    now     = now_iso()

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {"$set":        {"last_simulated_at": now, "updated_at": now},
         "$setOnInsert": {"shop_id": shop_id, "created_at": now}},
        upsert=True,
    )
    return {"ok": True, "last_simulated_at": now}


@router.get("/bot-settings/readiness")
async def get_readiness(request: Request):
    user = await require_user(request)
    shop = await _get_user_shop(user)
    return await _calculate_readiness(shop["shop_id"])
