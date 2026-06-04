"""Website chat widget adapter for Lapakin Asisten / Wabot.

This endpoint is for browser chat widgets, not WhatsApp provider webhooks.
It reuses the existing simulate/reply engine and returns the bot reply directly
to the website.
"""
from typing import Optional, Any
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from deps import db, now_iso, new_id
from routes.simulate import simulate, SimulateIn
from services.webchat_leads import process_webchat_lead

router = APIRouter()


class WebChatMessageIn(BaseModel):
    shop_id: str = Field(default="spacecraft-main")
    session_id: Optional[str] = None
    customer_name: Optional[str] = "Website Visitor"
    customer_phone: Optional[str] = None
    message: str
    page_url: Optional[str] = None


def _as_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return dict(value)


def _safe_session_id(raw: Optional[str]) -> str:
    raw = (raw or "").strip()
    raw = re.sub(r"[^a-zA-Z0-9_\-:.]", "", raw)
    if raw and len(raw) <= 96:
        return raw
    return f"web_{uuid.uuid4().hex[:16]}"


async def _write_event(shop_id: str, event_type: str, payload: dict):
    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": event_type,
        "payload": payload or {},
        "created_at": now_iso(),
    })


@router.post("/provider/webchat/message")
async def provider_webchat_message(data: WebChatMessageIn, request: Request):
    message = (data.message or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message wajib diisi.")

    if len(message) > 1500:
        raise HTTPException(status_code=413, detail="Message terlalu panjang.")

    shop = await db.shops.find_one({"shop_id": data.shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop tidak ditemukan.")

    if shop.get("is_active") is False:
        raise HTTPException(status_code=403, detail="Shop sedang tidak aktif.")

    session_id = _safe_session_id(data.session_id)
    customer_name = (data.customer_name or "Website Visitor").strip()[:120]
    customer_phone = (data.customer_phone or f"webchat:{session_id}").strip()[:120]

    sim_input = SimulateIn(
        shop_id=shop["shop_id"],
        session_id=session_id,
        customer_message=message,
        customer_name=customer_name,
        customer_phone=customer_phone,
    )

    sim_result = _as_dict(await simulate(sim_input))
    session_id = sim_result.get("session_id") or session_id

    # Mark simulator-created conversation as webchat context.
    await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop["shop_id"]},
        {
            "$set": {
                "source": "webchat",
                "provider": "webchat",
                "provider_customer_phone": customer_phone,
                "last_page_url": data.page_url,
                "last_origin": request.headers.get("origin"),
                "updated_at": now_iso(),
            }
        },
    )

    await db.messages.update_many(
        {"session_id": session_id, "shop_id": shop["shop_id"], "channel": "simulator"},
        {"$set": {"channel": "webchat"}},
    )

    lead_result = await process_webchat_lead(
        shop_id=shop["shop_id"],
        session_id=session_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        message=message,
        page_url=data.page_url,
        origin=request.headers.get("origin"),
        sim_result=sim_result,
    )

    bot_reply = sim_result.get("bot_reply") or ""
    if lead_result.get("reply_override"):
        bot_reply = lead_result["reply_override"]
    elif lead_result.get("append_reply"):
        bot_reply = (bot_reply.rstrip() + "\n\n" + lead_result["append_reply"]).strip()

    if lead_result.get("reply_override") or lead_result.get("append_reply"):
        now = now_iso()
        await db.messages.insert_one({
            "message_id": new_id("msg"),
            "session_id": session_id,
            "shop_id": shop["shop_id"],
            "role": "assistant",
            "channel": "webchat",
            "text": bot_reply,
            "intent": "lead_capture",
            "confidence": sim_result.get("confidence"),
            "source": "lead_capture",
            "metadata": {
                "lead_id": lead_result.get("lead_id"),
                "captured": lead_result.get("captured", False),
                "contact_requested": lead_result.get("contact_requested", False),
            },
            "created_at": now,
        })
        await db.sessions.update_one(
            {"session_id": session_id, "shop_id": shop["shop_id"]},
            {
                "$set": {
                    "last_reply": bot_reply,
                    "updated_at": now,
                },
                "$inc": {"message_count": 1},
            },
        )

    await _write_event(
        shop["shop_id"],
        "provider.webchat.message_received",
        {
            "session_id": session_id,
            "customer_phone": customer_phone,
            "customer_name": customer_name,
            "message": message,
            "page_url": data.page_url,
            "origin": request.headers.get("origin"),
            "reply_source": sim_result.get("source"),
            "intent": sim_result.get("intent"),
            "lead": {
                "lead_id": lead_result.get("lead_id"),
                "captured": lead_result.get("captured", False),
                "contact_requested": lead_result.get("contact_requested", False),
            },
        },
    )

    return {
        "ok": True,
        "provider": "webchat",
        "shop_id": shop["shop_id"],
        "shop_name": shop.get("name"),
        "session_id": session_id,
        "customer_message": message,
        "reply": bot_reply,
        "bot_reply": bot_reply,
        "intent": sim_result.get("intent"),
        "confidence": sim_result.get("confidence"),
        "handoff_required": sim_result.get("handoff_required") or lead_result.get("captured", False),
        "status": "handoff" if lead_result.get("captured") else sim_result.get("status"),
        "source": sim_result.get("source"),
        "product_card": sim_result.get("product_card"),
        "lead": lead_result,
    }
