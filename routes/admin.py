"""Admin monitoring routes for AI WA Bot."""
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from deps import db, require_admin, now_iso, new_id

router = APIRouter()


class AdminNoteIn(BaseModel):
    note: Optional[str] = None


def _today_start_iso() -> str:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()


async def _shop_map(shop_ids):
    ids = [x for x in set(shop_ids) if x]
    if not ids:
        return {}

    shops = await db.shops.find(
        {"shop_id": {"$in": ids}},
        {"_id": 0, "shop_id": 1, "name": 1, "source": 1, "owner_user_id": 1, "lapakin_shop_id": 1},
    ).to_list(len(ids))

    return {s["shop_id"]: s for s in shops}


async def _settings_map(shop_ids):
    ids = [x for x in set(shop_ids) if x]
    if not ids:
        return {}

    rows = await db.bot_settings.find(
        {"shop_id": {"$in": ids}},
        {"_id": 0},
    ).to_list(len(ids))

    return {s["shop_id"]: s for s in rows}


async def _session_counts_by_shop(shop_ids):
    ids = [x for x in set(shop_ids) if x]
    if not ids:
        return {}

    pipeline = [
        {"$match": {"shop_id": {"$in": ids}}},
        {"$group": {
            "_id": "$shop_id",
            "conversation_count": {"$sum": 1},
            "handoff_count": {
                "$sum": {
                    "$cond": [
                        {"$or": [
                            {"$eq": ["$status", "handoff"]},
                            {"$eq": ["$handoff_required", True]},
                        ]},
                        1,
                        0,
                    ]
                }
            },
            "resolved_count": {
                "$sum": {"$cond": [{"$eq": ["$status", "resolved"]}, 1, 0]}
            },
            "failed_count": {
                "$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}
            },
            "last_conversation_at": {"$max": "$updated_at"},
        }},
    ]

    rows = await db.sessions.aggregate(pipeline).to_list(1000)
    return {r["_id"]: r for r in rows}


@router.get("/admin/overview")
async def admin_overview(request: Request):
    await require_admin(request)

    today = _today_start_iso()

    total_users = await db.users.count_documents({})
    total_shops = await db.shops.count_documents({})
    standalone_shops = await db.shops.count_documents({"source": "standalone"})
    lapakin_shops = await db.shops.count_documents({"source": "lapakin"})

    bot_enabled = await db.bot_settings.count_documents({"enabled": True})
    auto_reply_active = await db.bot_settings.count_documents({
        "enabled": True,
        "mode": "auto_reply",
    })
    draft_only = await db.bot_settings.count_documents({
        "enabled": True,
        "mode": "draft_only",
    })

    total_sessions = await db.sessions.count_documents({})
    messages_today = await db.messages.count_documents({"created_at": {"$gte": today}})
    sessions_today = await db.sessions.count_documents({"created_at": {"$gte": today}})

    handoff_pending = await db.sessions.count_documents({
        "$or": [
            {"status": "handoff"},
            {"handoff_required": True},
        ],
        "status": {"$ne": "resolved"},
    })

    failed_conversations = await db.sessions.count_documents({"status": "failed"})

    recent = await db.sessions.find({}, {"_id": 0}) \
        .sort("updated_at", -1) \
        .limit(10) \
        .to_list(10)

    shop_ids = [x.get("shop_id") for x in recent]
    shops = await _shop_map(shop_ids)

    recent_items = []
    for item in recent:
        shop = shops.get(item.get("shop_id"), {})
        enriched = dict(item)
        enriched["shop_name"] = shop.get("name")
        enriched["shop_source"] = shop.get("source")
        enriched["lapakin_shop_id"] = shop.get("lapakin_shop_id")
        recent_items.append(enriched)

    top_pipeline = [
        {"$group": {
            "_id": "$shop_id",
            "conversation_count": {"$sum": 1},
            "message_count": {"$sum": {"$ifNull": ["$message_count", 0]}},
            "last_conversation_at": {"$max": "$updated_at"},
        }},
        {"$sort": {"conversation_count": -1}},
        {"$limit": 10},
    ]

    top_rows = await db.sessions.aggregate(top_pipeline).to_list(10)
    top_shop_ids = [x["_id"] for x in top_rows]
    top_shops_map = await _shop_map(top_shop_ids)

    top_shops = []
    for row in top_rows:
        shop = top_shops_map.get(row["_id"], {})
        top_shops.append({
            "shop_id": row["_id"],
            "shop_name": shop.get("name") or row["_id"],
            "shop_source": shop.get("source"),
            "conversation_count": row.get("conversation_count", 0),
            "message_count": row.get("message_count", 0),
            "last_conversation_at": row.get("last_conversation_at"),
        })

    return {
        "summary": {
            "total_users": total_users,
            "total_shops": total_shops,
            "standalone_shops": standalone_shops,
            "lapakin_shops": lapakin_shops,
            "bot_enabled": bot_enabled,
            "auto_reply_active": auto_reply_active,
            "draft_only": draft_only,
            "total_sessions": total_sessions,
            "sessions_today": sessions_today,
            "messages_today": messages_today,
            "handoff_pending": handoff_pending,
            "failed_conversations": failed_conversations,
        },
        "recent_conversations": recent_items,
        "top_shops": top_shops,
    }


@router.get("/admin/shops")
async def admin_shops(
    request: Request,
    source: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    await require_admin(request)

    query = {}

    if source:
        query["source"] = source

    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"shop_id": {"$regex": q, "$options": "i"}},
            {"whatsapp": {"$regex": q, "$options": "i"}},
            {"owner_email": {"$regex": q, "$options": "i"}},
        ]

    total = await db.shops.count_documents(query)

    shops = await db.shops.find(query, {"_id": 0}) \
        .sort("created_at", -1) \
        .skip(skip) \
        .limit(limit) \
        .to_list(limit)

    shop_ids = [s.get("shop_id") for s in shops]
    settings = await _settings_map(shop_ids)
    session_counts = await _session_counts_by_shop(shop_ids)

    items = []

    for shop in shops:
        sid = shop.get("shop_id")
        st = settings.get(sid, {})
        cnt = session_counts.get(sid, {})

        items.append({
            "shop_id": sid,
            "name": shop.get("name"),
            "source": shop.get("source"),
            "owner_user_id": shop.get("owner_user_id"),
            "owner_email": shop.get("owner_email") or shop.get("email"),
            "whatsapp": shop.get("whatsapp"),
            "lapakin_shop_id": shop.get("lapakin_shop_id"),
            "created_at": shop.get("created_at"),
            "bot": {
                "enabled": st.get("enabled", False),
                "mode": st.get("mode", "off"),
                "tone": st.get("tone"),
                "quota_monthly": st.get("quota_monthly"),
                "quota_used": st.get("quota_used"),
                "last_simulated_at": st.get("last_simulated_at"),
                "admin_disabled": st.get("admin_disabled", False),
                "admin_disable_reason": st.get("admin_disable_reason"),
                "admin_disabled_at": st.get("admin_disabled_at"),
                "admin_disabled_by": st.get("admin_disabled_by"),
            },
            "stats": {
                "conversation_count": cnt.get("conversation_count", 0),
                "handoff_count": cnt.get("handoff_count", 0),
                "resolved_count": cnt.get("resolved_count", 0),
                "failed_count": cnt.get("failed_count", 0),
                "last_conversation_at": cnt.get("last_conversation_at"),
            },
        })

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@router.get("/admin/conversations")
async def admin_conversations(
    request: Request,
    status: Optional[str] = Query(None),
    shop_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    await require_admin(request)

    query = {}

    if status:
        query["status"] = status

    if shop_id:
        query["shop_id"] = shop_id

    if q:
        query["$or"] = [
            {"customer_name": {"$regex": q, "$options": "i"}},
            {"customer_phone": {"$regex": q, "$options": "i"}},
            {"last_message": {"$regex": q, "$options": "i"}},
            {"last_reply": {"$regex": q, "$options": "i"}},
            {"shop_id": {"$regex": q, "$options": "i"}},
        ]

    total = await db.sessions.count_documents(query)

    sessions = await db.sessions.find(query, {"_id": 0}) \
        .sort("updated_at", -1) \
        .skip(skip) \
        .limit(limit) \
        .to_list(limit)

    shop_ids = [x.get("shop_id") for x in sessions]
    shops = await _shop_map(shop_ids)

    items = []
    for item in sessions:
        shop = shops.get(item.get("shop_id"), {})
        enriched = dict(item)
        enriched["shop_name"] = shop.get("name")
        enriched["shop_source"] = shop.get("source")
        enriched["lapakin_shop_id"] = shop.get("lapakin_shop_id")
        items.append(enriched)

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@router.get("/admin/conversations/{session_id}")
async def admin_conversation_detail(session_id: str, request: Request):
    await require_admin(request)

    session = await db.sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan")

    messages = await db.messages.find(
        {"session_id": session_id},
        {"_id": 0},
    ).sort("created_at", 1).to_list(500)

    shop = await db.shops.find_one(
        {"shop_id": session.get("shop_id")},
        {"_id": 0},
    ) or {}

    return {
        "session": session,
        "messages": messages,
        "shop": shop,
    }


@router.post("/admin/conversations/{session_id}/handoff")
async def admin_mark_handoff(session_id: str, data: AdminNoteIn, request: Request):
    admin = await require_admin(request)
    now = now_iso()

    session = await db.sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan")

    result = await db.sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "status": "handoff",
                "handoff_required": True,
                "updated_at": now,
            },
            "$inc": {"message_count": 1},
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan")

    await db.messages.insert_one({
        "message_id": new_id("msg"),
        "session_id": session_id,
        "shop_id": session.get("shop_id"),
        "role": "system",
        "channel": "admin_dashboard",
        "text": data.note or "Admin menandai conversation perlu handoff.",
        "intent": "admin_handoff_marked",
        "confidence": None,
        "source": "admin_action",
        "metadata": {
            "action": "admin_handoff",
            "admin_user_id": admin.get("user_id"),
            "admin_email": admin.get("email"),
        },
        "created_at": now,
    })

    return {"ok": True, "status": "handoff"}


@router.post("/admin/conversations/{session_id}/resolve")
async def admin_resolve(session_id: str, data: AdminNoteIn, request: Request):
    admin = await require_admin(request)
    now = now_iso()

    session = await db.sessions.find_one({"session_id": session_id}, {"_id": 0})
    if not session:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan")

    update_doc = {
        "$set": {
            "status": "resolved",
            "handoff_required": False,
            "resolved_at": now,
            "updated_at": now,
        }
    }

    if data.note:
        update_doc["$inc"] = {"message_count": 1}

    result = await db.sessions.update_one({"session_id": session_id}, update_doc)

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan")

    if data.note:
        await db.messages.insert_one({
            "message_id": new_id("msg"),
            "session_id": session_id,
            "shop_id": session.get("shop_id"),
            "role": "system",
            "channel": "admin_dashboard",
            "text": data.note,
            "intent": "admin_internal_note",
            "confidence": None,
            "source": "admin_note",
            "metadata": {
                "action": "admin_resolved",
                "admin_user_id": admin.get("user_id"),
                "admin_email": admin.get("email"),
            },
            "created_at": now,
        })

    return {"ok": True, "status": "resolved"}


# ── Safety Control / Kill Switch ──────────────────────────

class SystemStatusIn(BaseModel):
    status: str
    reason: Optional[str] = None


class ShopForceIn(BaseModel):
    reason: Optional[str] = None


VALID_SYSTEM_STATUS = {"on", "maintenance", "off"}


async def _get_system_control() -> dict:
    doc = await db.system_settings.find_one(
        {"key": "lapakin_asisten_control"},
        {"_id": 0},
    )

    if not doc:
        return {
            "key": "lapakin_asisten_control",
            "status": "on",
            "reason": "",
            "updated_at": None,
            "updated_by": None,
        }

    return doc


@router.get("/admin/system-status")
async def admin_get_system_status(request: Request):
    await require_admin(request)
    control = await _get_system_control()

    return {
        "status": control.get("status", "on"),
        "reason": control.get("reason", ""),
        "updated_at": control.get("updated_at"),
        "updated_by": control.get("updated_by"),
        "auto_reply_allowed": control.get("status", "on") == "on",
    }


@router.put("/admin/system-status")
async def admin_update_system_status(data: SystemStatusIn, request: Request):
    admin = await require_admin(request)
    status = (data.status or "").lower().strip()

    if status not in VALID_SYSTEM_STATUS:
        raise HTTPException(
            status_code=400,
            detail="Status tidak valid. Gunakan: on, maintenance, off."
        )

    if status != "on" and not data.reason:
        raise HTTPException(
            status_code=400,
            detail="Reason wajib diisi saat maintenance/off."
        )

    now = now_iso()

    await db.system_settings.update_one(
        {"key": "lapakin_asisten_control"},
        {
            "$set": {
                "key": "lapakin_asisten_control",
                "status": status,
                "reason": data.reason or "",
                "updated_at": now,
                "updated_by": admin.get("email") or admin.get("user_id"),
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": None,
        "type": "system.status_updated",
        "payload": {
            "status": status,
            "reason": data.reason or "",
            "admin_user_id": admin.get("user_id"),
            "admin_email": admin.get("email"),
        },
        "created_at": now,
    })

    return {
        "ok": True,
        "status": status,
        "reason": data.reason or "",
    }


@router.post("/admin/shops/{shop_id}/force-disable")
async def admin_force_disable_shop(shop_id: str, data: ShopForceIn, request: Request):
    admin = await require_admin(request)

    if not data.reason:
        raise HTTPException(status_code=400, detail="Reason wajib diisi.")

    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop tidak ditemukan")

    now = now_iso()

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {
            "$set": {
                "shop_id": shop_id,
                "enabled": False,
                "admin_disabled": True,
                "admin_disable_reason": data.reason,
                "admin_disabled_at": now,
                "admin_disabled_by": admin.get("email") or admin.get("user_id"),
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
                "mode": "off",
                "tone": "ramah",
                "language": "id",
                "quota_monthly": 100,
                "quota_used": 0,
            },
        },
        upsert=True,
    )

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "shop.force_disabled",
        "payload": {
            "reason": data.reason,
            "shop_name": shop.get("name"),
            "admin_user_id": admin.get("user_id"),
            "admin_email": admin.get("email"),
        },
        "created_at": now,
    })

    return {"ok": True, "shop_id": shop_id, "admin_disabled": True}


@router.post("/admin/shops/{shop_id}/force-enable")
async def admin_force_enable_shop(shop_id: str, data: ShopForceIn, request: Request):
    admin = await require_admin(request)

    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop tidak ditemukan")

    now = now_iso()

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {
            "$set": {
                "shop_id": shop_id,
                "enabled": True,
                "admin_disabled": False,
                "admin_reenabled_at": now,
                "admin_reenabled_by": admin.get("email") or admin.get("user_id"),
                "admin_reenable_reason": data.reason or "",
                "updated_at": now,
            },
            "$unset": {
                "admin_disable_reason": "",
            },
            "$setOnInsert": {
                "created_at": now,
                "mode": "draft_only",
                "tone": "ramah",
                "language": "id",
                "quota_monthly": 100,
                "quota_used": 0,
            },
        },
        upsert=True,
    )

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "shop.force_enabled",
        "payload": {
            "reason": data.reason or "",
            "shop_name": shop.get("name"),
            "admin_user_id": admin.get("user_id"),
            "admin_email": admin.get("email"),
        },
        "created_at": now,
    })

    return {"ok": True, "shop_id": shop_id, "admin_disabled": False}


# ── Auto-reply Safety Policy ──────────────────────────────

class PolicyEvaluateIn(BaseModel):
    intent: Optional[str] = "general_inquiry"
    confidence: Optional[str] = "medium"
    handoff_required: bool = False
    channel: Optional[str] = "whatsapp"
    require_provider_ready: bool = True


@router.post("/admin/shops/{shop_id}/policy-evaluate")
async def admin_policy_evaluate(shop_id: str, data: PolicyEvaluateIn, request: Request):
    await require_admin(request)

    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop tidak ditemukan")

    from safety_policy import evaluate_auto_reply_policy

    result = await evaluate_auto_reply_policy(
        shop_id=shop_id,
        session_id=None,
        reply_result={
            "intent": data.intent,
            "confidence": data.confidence,
            "handoff_required": data.handoff_required,
        },
        channel=data.channel or "whatsapp",
        require_provider_ready=data.require_provider_ready,
        write_event=True,
    )

    return result


# ── Bot Events / Admin Alerts ─────────────────────────────

@router.get("/admin/events")
async def admin_events(
    request: Request,
    type: Optional[str] = Query(None),
    shop_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(80, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    await require_admin(request)

    query = {}

    if type:
        query["type"] = type

    if shop_id:
        query["shop_id"] = shop_id

    if q:
        query["$or"] = [
            {"event_id": {"$regex": q, "$options": "i"}},
            {"type": {"$regex": q, "$options": "i"}},
            {"shop_id": {"$regex": q, "$options": "i"}},
            {"payload.reason": {"$regex": q, "$options": "i"}},
            {"payload.code": {"$regex": q, "$options": "i"}},
            {"payload.status": {"$regex": q, "$options": "i"}},
            {"payload.action": {"$regex": q, "$options": "i"}},
            {"payload.admin_email": {"$regex": q, "$options": "i"}},
        ]

    total = await db.bot_events.count_documents(query)

    rows = await db.bot_events.find(query, {"_id": 0}) \
        .sort("created_at", -1) \
        .skip(skip) \
        .limit(limit) \
        .to_list(limit)

    shop_ids = [x.get("shop_id") for x in rows if x.get("shop_id")]
    shops = await _shop_map(shop_ids)

    items = []
    for row in rows:
        enriched = dict(row)
        shop = shops.get(row.get("shop_id"), {})
        enriched["shop_name"] = shop.get("name")
        enriched["shop_source"] = shop.get("source")
        items.append(enriched)

    # lightweight type distribution for filters/overview
    type_pipeline = [
        {"$group": {"_id": "$type", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 30},
    ]
    type_rows = await db.bot_events.aggregate(type_pipeline).to_list(30)

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "skip": skip,
        "event_types": [
            {"type": x.get("_id") or "unknown", "count": x.get("count", 0)}
            for x in type_rows
        ],
    }


@router.get("/admin/events/{event_id}")
async def admin_event_detail(event_id: str, request: Request):
    await require_admin(request)

    event = await db.bot_events.find_one({"event_id": event_id}, {"_id": 0})
    if not event:
        raise HTTPException(status_code=404, detail="Event tidak ditemukan")

    shop = {}
    if event.get("shop_id"):
        shop = await db.shops.find_one({"shop_id": event.get("shop_id")}, {"_id": 0}) or {}

    return {
        "event": event,
        "shop": shop,
    }


# ── SpaceCraft Sync Admin API ─────────────────────────────

@router.post("/admin/spacecraft/sync-products")
async def admin_spacecraft_sync_products(request: Request):
    admin = await require_admin(request)

    from services.spacecraft_product_sync import sync_spacecraft_products

    result = await sync_spacecraft_products()
    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": result.get("shop_id", "spacecraft-main"),
        "type": "admin.spacecraft.products_sync_triggered",
        "payload": {
            "admin_user_id": admin.get("user_id"),
            "admin_email": admin.get("email"),
            "result": result,
        },
        "created_at": now_iso(),
    })
    return result


@router.get("/admin/spacecraft/sync-status")
async def admin_spacecraft_sync_status(request: Request):
    await require_admin(request)

    last_success = await db.bot_events.find_one(
        {"type": "spacecraft.products_synced"},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    last_failure = await db.bot_events.find_one(
        {"type": "spacecraft.products_sync_failed"},
        {"_id": 0},
        sort=[("created_at", -1)],
    )

    product_count = await db.products.count_documents({
        "shop_id": "spacecraft-main",
        "source": "spacecraft_api",
        "status": "active",
    })

    return {
        "ok": True,
        "auto_sync_enabled": os.environ.get("SPACECRAFT_AUTO_SYNC_ENABLED", "true"),
        "auto_sync_seconds": int(os.environ.get("SPACECRAFT_AUTO_SYNC_SECONDS", "1800")),
        "active_product_count": product_count,
        "last_success": last_success,
        "last_failure": last_failure,
    }


# ── Webchat Leads Admin API ───────────────────────────────

@router.get("/admin/webchat-leads")
async def admin_webchat_leads(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    await require_admin(request)

    query = {"source": "webchat"}

    if status:
        query["status"] = status

    if q:
        query["$or"] = [
            {"customer_name": {"$regex": q, "$options": "i"}},
            {"customer_phone": {"$regex": q, "$options": "i"}},
            {"need_summary": {"$regex": q, "$options": "i"}},
            {"conversation_summary": {"$regex": q, "$options": "i"}},
            {"session_id": {"$regex": q, "$options": "i"}},
        ]

    total = await db.webchat_leads.count_documents(query)
    rows = await db.webchat_leads.find(query, {"_id": 0}) \
        .sort("updated_at", -1) \
        .skip(skip) \
        .limit(limit) \
        .to_list(limit)

    status_rows = await db.webchat_leads.aggregate([
        {"$match": {"source": "webchat"}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]).to_list(50)

    return {
        "items": rows,
        "total": total,
        "limit": limit,
        "skip": skip,
        "status_counts": [
            {"status": x.get("_id") or "unknown", "count": x.get("count", 0)}
            for x in status_rows
        ],
    }


@router.get("/admin/webchat-leads/{lead_id}")
async def admin_webchat_lead_detail(lead_id: str, request: Request):
    await require_admin(request)

    lead = await db.webchat_leads.find_one({"lead_id": lead_id}, {"_id": 0})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead tidak ditemukan")

    session = {}
    messages = []
    if lead.get("session_id"):
        session = await db.sessions.find_one({"session_id": lead.get("session_id")}, {"_id": 0}) or {}
        messages = await db.messages.find(
            {"session_id": lead.get("session_id")},
            {"_id": 0},
        ).sort("created_at", 1).to_list(500)

    return {
        "lead": lead,
        "session": session,
        "messages": messages,
    }


@router.post("/admin/webchat-leads/{lead_id}/mark-followed-up")
async def admin_webchat_lead_mark_followed_up(lead_id: str, data: AdminNoteIn, request: Request):
    admin = await require_admin(request)
    now = now_iso()

    lead = await db.webchat_leads.find_one({"lead_id": lead_id}, {"_id": 0})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead tidak ditemukan")

    await db.webchat_leads.update_one(
        {"lead_id": lead_id},
        {
            "$set": {
                "status": "followed_up",
                "followed_up_at": now,
                "followed_up_by": admin.get("email"),
                "follow_up_note": data.note,
                "updated_at": now,
            }
        },
    )

    if lead.get("session_id"):
        await db.sessions.update_one(
            {"session_id": lead.get("session_id")},
            {
                "$set": {
                    "status": "resolved",
                    "handoff_required": False,
                    "resolved_at": now,
                    "updated_at": now,
                }
            },
        )

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": lead.get("shop_id"),
        "type": "webchat.lead_followed_up",
        "payload": {
            "lead_id": lead_id,
            "admin_user_id": admin.get("user_id"),
            "admin_email": admin.get("email"),
            "note": data.note,
        },
        "created_at": now,
    })

    return {"ok": True, "lead_id": lead_id, "status": "followed_up"}
