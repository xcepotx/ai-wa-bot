"""Owner webchat lead routes - tenant-safe by user.shop_id."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from deps import db, require_user, now_iso, new_id


router = APIRouter()


class LeadNoteIn(BaseModel):
    note: Optional[str] = None


async def _get_user_shop_id(user: dict) -> str:
    shop_id = user.get("shop_id")
    if not shop_id:
        raise HTTPException(status_code=404, detail="Belum punya toko")
    return shop_id


@router.get("/webchat-leads")
async def list_webchat_leads(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    skip: int = Query(0, ge=0),
):
    user = await require_user(request)
    shop_id = await _get_user_shop_id(user)

    query = {"shop_id": shop_id, "source": "webchat"}

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
    items = await db.webchat_leads.find(query, {"_id": 0}) \
        .sort("updated_at", -1) \
        .skip(skip) \
        .limit(limit) \
        .to_list(limit)

    status_rows = await db.webchat_leads.aggregate([
        {"$match": {"shop_id": shop_id, "source": "webchat"}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ]).to_list(50)

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "skip": skip,
        "status_counts": [
            {"status": row.get("_id") or "unknown", "count": row.get("count", 0)}
            for row in status_rows
        ],
    }


@router.get("/webchat-leads/{lead_id}")
async def get_webchat_lead(lead_id: str, request: Request):
    user = await require_user(request)
    shop_id = await _get_user_shop_id(user)

    lead = await db.webchat_leads.find_one(
        {"lead_id": lead_id, "shop_id": shop_id},
        {"_id": 0},
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead tidak ditemukan")

    session = {}
    messages = []

    if lead.get("session_id"):
        session = await db.sessions.find_one(
            {"session_id": lead.get("session_id"), "shop_id": shop_id},
            {"_id": 0},
        ) or {}

        messages = await db.messages.find(
            {"session_id": lead.get("session_id"), "shop_id": shop_id},
            {"_id": 0},
        ).sort("created_at", 1).to_list(500)

    return {
        "lead": lead,
        "session": session,
        "messages": messages,
    }


@router.post("/webchat-leads/{lead_id}/mark-followed-up")
async def mark_webchat_lead_followed_up(lead_id: str, data: LeadNoteIn, request: Request):
    user = await require_user(request)
    shop_id = await _get_user_shop_id(user)
    now = now_iso()

    lead = await db.webchat_leads.find_one(
        {"lead_id": lead_id, "shop_id": shop_id},
        {"_id": 0},
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead tidak ditemukan")

    await db.webchat_leads.update_one(
        {"lead_id": lead_id, "shop_id": shop_id},
        {
            "$set": {
                "status": "followed_up",
                "followed_up_at": now,
                "followed_up_by": user.get("email"),
                "follow_up_note": data.note,
                "updated_at": now,
            }
        },
    )

    if lead.get("session_id"):
        await db.sessions.update_one(
            {"session_id": lead.get("session_id"), "shop_id": shop_id},
            {
                "$set": {
                    "status": "resolved",
                    "handoff_required": False,
                    "resolved_at": now,
                    "updated_at": now,
                }
            },
        )

        await db.messages.insert_one({
            "message_id": new_id("msg"),
            "session_id": lead.get("session_id"),
            "shop_id": shop_id,
            "role": "system",
            "channel": "dashboard",
            "text": data.note or "Lead ditandai sudah di-follow up.",
            "intent": "lead_followed_up",
            "confidence": None,
            "source": "owner_action",
            "metadata": {
                "lead_id": lead_id,
                "action": "mark_followed_up",
                "user_id": user.get("user_id"),
            },
            "created_at": now,
        })

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "webchat.lead_followed_up",
        "payload": {
            "lead_id": lead_id,
            "user_id": user.get("user_id"),
            "email": user.get("email"),
            "note": data.note,
        },
        "created_at": now,
    })

    return {"ok": True, "lead_id": lead_id, "status": "followed_up"}
