"""Mock WhatsApp provider adapter for Lapakin Asisten.

This route simulates a real provider webhook without sending messages to WhatsApp.
It is used before Meta Cloud API integration to validate the full provider flow.
"""
from typing import Optional, Dict, Any
import uuid

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from deps import db, now_iso, new_id, require_admin
from routes.simulate import simulate, SimulateIn
from safety_policy import evaluate_auto_reply_policy, mark_auto_reply_sent

router = APIRouter()


class MockWebhookIn(BaseModel):
    shop_id: Optional[str] = None
    provider_phone: Optional[str] = None
    customer_phone: str
    customer_name: Optional[str] = "Pelanggan Mock"
    message: str
    provider_message_id: Optional[str] = None


async def _find_shop(data: MockWebhookIn) -> dict:
    if data.shop_id:
        shop = await db.shops.find_one({"shop_id": data.shop_id}, {"_id": 0})
        if not shop:
            raise HTTPException(status_code=404, detail="Shop tidak ditemukan")
        return shop

    if data.provider_phone:
        candidates = [
            data.provider_phone,
            data.provider_phone.replace("+", ""),
            "+" + data.provider_phone.replace("+", ""),
        ]

        shop = await db.shops.find_one(
            {
                "$or": [
                    {"whatsapp": {"$in": candidates}},
                    {"whatsapp_number": {"$in": candidates}},
                    {"phone": {"$in": candidates}},
                ]
            },
            {"_id": 0},
        )
        if shop:
            return shop

    raise HTTPException(
        status_code=400,
        detail="shop_id atau provider_phone wajib valid untuk mock webhook."
    )


async def _find_existing_session(shop_id: str, customer_phone: str) -> Optional[dict]:
    return await db.sessions.find_one(
        {
            "shop_id": shop_id,
            "customer_phone": customer_phone,
            "source": "whatsapp_mock",
            "status": {"$ne": "resolved"},
        },
        {"_id": 0},
        sort=[("updated_at", -1)],
    )


def _as_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return dict(value)


async def _write_provider_event(shop_id: str, event_type: str, payload: dict):
    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": event_type,
        "payload": payload or {},
        "created_at": now_iso(),
    })


async def _store_provider_message(
    *,
    shop_id: str,
    session_id: str,
    direction: str,
    provider_message_id: str,
    customer_phone: str,
    customer_name: str,
    text: str,
    status: str,
    payload: Optional[dict] = None,
):
    doc = {
        "provider_message_id": provider_message_id,
        "provider": "mock",
        "channel": "whatsapp_mock",
        "shop_id": shop_id,
        "session_id": session_id,
        "direction": direction,
        "customer_phone": customer_phone,
        "customer_name": customer_name,
        "text": text,
        "status": status,
        "payload": payload or {},
        "created_at": now_iso(),
    }

    await db.provider_messages.insert_one(doc)
    return doc


async def _update_latest_bot_message(session_id: str, policy_result: dict, provider_status: str):
    msg = await db.messages.find_one(
        {
            "session_id": session_id,
            "role": {"$in": ["bot", "system"]},
        },
        {"_id": 0, "message_id": 1, "metadata": 1},
        sort=[("created_at", -1)],
    )

    if not msg:
        return

    metadata = msg.get("metadata") or {}
    metadata["provider_mock"] = {
        "provider_status": provider_status,
        "policy": policy_result,
        "updated_at": now_iso(),
    }

    await db.messages.update_one(
        {"message_id": msg["message_id"]},
        {"$set": {"metadata": metadata}},
    )


@router.post("/provider/mock/webhook")
async def provider_mock_webhook(data: MockWebhookIn):
    """Simulate inbound WhatsApp message.

    This endpoint intentionally uses the same reply engine as simulator, then
    runs auto-reply policy before deciding whether the mock reply is "sent".
    """
    shop = await _find_shop(data)
    shop_id = shop["shop_id"]

    provider_message_id = data.provider_message_id or f"mock_in_{uuid.uuid4().hex[:16]}"

    existing_provider_msg = await db.provider_messages.find_one(
        {
            "provider": "mock",
            "direction": "inbound",
            "provider_message_id": provider_message_id,
            "shop_id": shop_id,
        },
        {"_id": 0},
    )

    if existing_provider_msg:
        return {
            "ok": True,
            "duplicate": True,
            "shop_id": shop_id,
            "session_id": existing_provider_msg.get("session_id"),
            "provider_message_id": provider_message_id,
            "status": existing_provider_msg.get("status"),
        }

    existing_session = await _find_existing_session(shop_id, data.customer_phone)

    sim_input = SimulateIn(
        shop_id=shop_id,
        session_id=existing_session.get("session_id") if existing_session else None,
        customer_message=data.message,
        customer_name=data.customer_name or "Pelanggan Mock",
        customer_phone=data.customer_phone,
    )

    sim_result = _as_dict(await simulate(sim_input))
    session_id = sim_result["session_id"]

    # Convert simulator-created session/messages into mock provider context.
    await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop_id},
        {
            "$set": {
                "source": "whatsapp_mock",
                "provider": "mock",
                "provider_customer_phone": data.customer_phone,
                "last_provider_message_id": provider_message_id,
                "updated_at": now_iso(),
            }
        },
    )

    await db.messages.update_many(
        {"session_id": session_id, "shop_id": shop_id, "channel": "simulator"},
        {"$set": {"channel": "whatsapp_mock"}},
    )

    await _store_provider_message(
        shop_id=shop_id,
        session_id=session_id,
        direction="inbound",
        provider_message_id=provider_message_id,
        customer_phone=data.customer_phone,
        customer_name=data.customer_name or "Pelanggan Mock",
        text=data.message,
        status="received",
        payload={
            "provider_phone": data.provider_phone,
            "shop_name": shop.get("name"),
        },
    )

    await _write_provider_event(
        shop_id,
        "provider.mock.webhook_received",
        {
            "session_id": session_id,
            "provider_message_id": provider_message_id,
            "customer_phone": data.customer_phone,
            "message": data.message,
        },
    )

    if sim_result.get("source") == "safety_policy" or sim_result.get("status") == "skipped":
        policy_result = {
            "allowed": False,
            "action": "skipped_by_reply_engine",
            "status": "skipped",
            "reason": sim_result.get("bot_reply"),
            "code": sim_result.get("intent"),
            "channel": "whatsapp_mock",
        }
    else:
        policy_result = await evaluate_auto_reply_policy(
            shop_id=shop_id,
            session_id=session_id,
            reply_result={
                "intent": sim_result.get("intent"),
                "confidence": sim_result.get("confidence"),
                "handoff_required": sim_result.get("handoff_required"),
            },
            channel="whatsapp_mock",
            require_provider_ready=True,
            write_event=True,
        )

    if policy_result.get("allowed"):
        provider_status = "sent_mock"
        session_status = "bot_replied"
        event_type = "provider.mock.reply_sent"
        await mark_auto_reply_sent(shop_id, session_id)
    else:
        action = policy_result.get("action")
        if action == "handoff_required":
            provider_status = "handoff"
            session_status = "handoff"
            event_type = "provider.mock.reply_handoff"
        elif action == "draft_only":
            provider_status = "draft_only"
            session_status = "draft_only"
            event_type = "provider.mock.reply_draft_only"
        else:
            provider_status = "skipped"
            session_status = "skipped"
            event_type = "provider.mock.reply_skipped"

    outbound_id = f"mock_out_{uuid.uuid4().hex[:16]}"

    await _store_provider_message(
        shop_id=shop_id,
        session_id=session_id,
        direction="outbound",
        provider_message_id=outbound_id,
        customer_phone=data.customer_phone,
        customer_name=data.customer_name or "Pelanggan Mock",
        text=sim_result.get("bot_reply") or "",
        status=provider_status,
        payload={
            "policy": policy_result,
            "reply_result": sim_result,
        },
    )

    await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop_id},
        {
            "$set": {
                "status": session_status,
                "provider_status": provider_status,
                "provider_policy_action": policy_result.get("action"),
                "provider_policy_reason": policy_result.get("reason"),
                "updated_at": now_iso(),
            }
        },
    )

    await _update_latest_bot_message(session_id, policy_result, provider_status)

    await _write_provider_event(
        shop_id,
        event_type,
        {
            "session_id": session_id,
            "inbound_provider_message_id": provider_message_id,
            "outbound_provider_message_id": outbound_id,
            "provider_status": provider_status,
            "policy": policy_result,
        },
    )

    return {
        "ok": True,
        "duplicate": False,
        "provider": "mock",
        "shop_id": shop_id,
        "shop_name": shop.get("name"),
        "session_id": session_id,
        "customer_phone": data.customer_phone,
        "inbound_provider_message_id": provider_message_id,
        "outbound_provider_message_id": outbound_id,
        "customer_message": data.message,
        "bot_reply": sim_result.get("bot_reply"),
        "reply_source": sim_result.get("source"),
        "intent": sim_result.get("intent"),
        "confidence": sim_result.get("confidence"),
        "provider_status": provider_status,
        "policy": policy_result,
    }


@router.get("/provider/mock/sent-messages")
async def provider_mock_sent_messages(
    request: Request,
    shop_id: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(80, ge=1, le=200),
):
    await require_admin(request)

    query: Dict[str, Any] = {"provider": "mock"}

    if shop_id:
        query["shop_id"] = shop_id
    if direction:
        query["direction"] = direction
    if status:
        query["status"] = status

    items = await db.provider_messages.find(query, {"_id": 0}) \
        .sort("created_at", -1) \
        .limit(limit) \
        .to_list(limit)

    return {
        "items": items,
        "total": len(items),
    }


@router.post("/provider/mock/reset")
async def provider_mock_reset(request: Request, shop_id: Optional[str] = None):
    await require_admin(request)

    query: Dict[str, Any] = {"provider": "mock"}
    if shop_id:
        query["shop_id"] = shop_id

    result = await db.provider_messages.delete_many(query)

    return {
        "ok": True,
        "deleted": result.deleted_count,
        "shop_id": shop_id,
    }
