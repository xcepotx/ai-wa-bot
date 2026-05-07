"""Conversation inbox routes - owner can view and manage bot sessions."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from deps import db, require_user, now_iso, new_id

router = APIRouter()


class ResolveIn(BaseModel):
    note: Optional[str] = None


class HandoffIn(BaseModel):
    note: Optional[str] = None


async def _get_user_shop_id(user: dict) -> str:
    shop_id = user.get("shop_id")
    if not shop_id:
        raise HTTPException(status_code=404, detail="Belum punya toko")
    return shop_id


@router.get("/conversations")
async def list_conversations(
    request: Request,
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
    skip: int = Query(0, ge=0),
):
    user = await require_user(request)
    shop_id = await _get_user_shop_id(user)

    query = {"shop_id": shop_id}

    if status:
        query["status"] = status

    if q:
        query["$or"] = [
            {"customer_name": {"$regex": q, "$options": "i"}},
            {"customer_phone": {"$regex": q, "$options": "i"}},
            {"last_message": {"$regex": q, "$options": "i"}},
            {"last_reply": {"$regex": q, "$options": "i"}},
        ]

    total = await db.sessions.count_documents(query)

    items = await db.sessions.find(query, {"_id": 0}) \
        .sort("updated_at", -1) \
        .skip(skip) \
        .limit(limit) \
        .to_list(limit)

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "skip": skip,
    }


@router.get("/conversations/{session_id}")
async def get_conversation(session_id: str, request: Request):
    user = await require_user(request)
    shop_id = await _get_user_shop_id(user)

    session = await db.sessions.find_one(
        {"session_id": session_id, "shop_id": shop_id},
        {"_id": 0},
    )

    if not session:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan")

    messages = await db.messages.find(
        {"session_id": session_id, "shop_id": shop_id},
        {"_id": 0},
    ).sort("created_at", 1).to_list(500)

    return {
        "session": session,
        "messages": messages,
    }


@router.post("/conversations/{session_id}/resolve")
async def resolve_conversation(session_id: str, data: ResolveIn, request: Request):
    user = await require_user(request)
    shop_id = await _get_user_shop_id(user)
    now = now_iso()

    result = await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop_id},
        {
            "$set": {
                "status": "resolved",
                "handoff_required": False,
                "resolved_at": now,
                "updated_at": now,
            },
            "$inc": {"message_count": 1} if data.note else {},
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan")

    if data.note:
        await db.messages.insert_one({
            "message_id": new_id("msg"),
            "session_id": session_id,
            "shop_id": shop_id,
            "role": "system",
            "channel": "dashboard",
            "text": data.note,
            "intent": "internal_note",
            "confidence": None,
            "source": "owner_note",
            "metadata": {
                "action": "resolved",
                "user_id": user.get("user_id"),
            },
            "created_at": now,
        })

    return {"ok": True, "status": "resolved"}


@router.post("/conversations/{session_id}/handoff")
async def mark_handoff(session_id: str, data: HandoffIn, request: Request):
    user = await require_user(request)
    shop_id = await _get_user_shop_id(user)
    now = now_iso()

    result = await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop_id},
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
        "shop_id": shop_id,
        "role": "system",
        "channel": "dashboard",
        "text": data.note or "Ditandai perlu handoff ke owner.",
        "intent": "handoff_marked",
        "confidence": None,
        "source": "owner_action",
        "metadata": {
            "action": "handoff",
            "user_id": user.get("user_id"),
        },
        "created_at": now,
    })

    return {"ok": True, "status": "handoff"}
