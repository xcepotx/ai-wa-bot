"""Simulator route - test bot without real WhatsApp + persist sessions/messages."""
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from context_service import get_shop_context
from prompt_builder import build_system_prompt
from deps import db, now_iso, new_id

logger = logging.getLogger("ai-wa-bot")
router = APIRouter()


class SimulateIn(BaseModel):
    shop_id: str
    customer_message: str
    customer_name: Optional[str] = "Pelanggan"
    customer_phone: Optional[str] = None
    session_id: Optional[str] = None


class SimulateOut(BaseModel):
    session_id: str
    shop_id: str
    customer_message: str
    bot_reply: str
    intent: str
    confidence: str
    handoff_required: bool
    source: str
    status: str
    response_ms: int


@router.post("/simulate", response_model=SimulateOut)
async def simulate(data: SimulateIn):
    import time

    t0 = time.time()

    context = await get_shop_context(data.shop_id)
    if not context:
        raise HTTPException(
            status_code=404,
            detail=f"Toko {data.shop_id} tidak ditemukan."
        )

    session_id = data.session_id or f"sim_{uuid.uuid4().hex[:8]}"
    now = now_iso()

    await _ensure_session(
        session_id=session_id,
        shop_id=data.shop_id,
        customer_name=data.customer_name or "Pelanggan",
        customer_phone=data.customer_phone,
        now=now,
    )

    previous_history = await _build_recent_history(session_id, data.shop_id)

    await _insert_message(
        session_id=session_id,
        shop_id=data.shop_id,
        role="customer",
        channel="simulator",
        text=data.customer_message,
        intent=None,
        confidence=None,
        source="customer",
        metadata={},
    )

    bot_settings = context.get("bot_settings", {})
    handoff_kw = bot_settings.get("handoff_keywords", [])
    fallback_msg = bot_settings.get(
        "fallback_message", "Maaf kak, silakan hubungi admin kami ya 🙏"
    )

    msg_lower = data.customer_message.lower()

    bot_reply = ""
    intent = "general_inquiry"
    confidence = "medium"
    source = "llm"
    handoff_required = False
    status = "bot_replied"

    for kw in handoff_kw:
        if kw and kw.lower() in msg_lower:
            bot_reply = fallback_msg
            intent = "handoff_request"
            confidence = "high"
            source = "keyword_match"
            handoff_required = True
            status = "handoff"
            break

    if not bot_reply:
        faq_reply = _find_faq_match(data.customer_message, context.get("faqs", []))
        if faq_reply:
            bot_reply = faq_reply
            intent = "faq_match"
            confidence = "high"
            source = "faq"
            handoff_required = False
            status = "bot_replied"

    if not bot_reply:
        system_prompt = build_system_prompt(context)
        user_msg = (
            "Riwayat percakapan sebelumnya:\n"
            f"{previous_history}\n\n"
            f"Pelanggan ({data.customer_name}): {data.customer_message}"
        )

        try:
            from llm_service import chat_text
            bot_reply = await chat_text(system_prompt, user_msg)
            source = "llm"
            intent = _guess_intent(data.customer_message)
            confidence = "medium"
        except Exception as e:
            logger.error("LLM error: %s", e)
            bot_reply = fallback_msg
            source = "fallback"
            intent = "unknown"
            confidence = "low"
            status = "failed"

        handoff_required = _detect_handoff_in_reply(bot_reply)
        if handoff_required:
            status = "handoff"

    response_ms = int((time.time() - t0) * 1000)

    await _insert_message(
        session_id=session_id,
        shop_id=data.shop_id,
        role="bot",
        channel="simulator",
        text=bot_reply,
        intent=intent,
        confidence=confidence,
        source=source,
        metadata={
            "handoff_required": handoff_required,
            "response_ms": response_ms,
        },
    )

    await _update_session_after_reply(
        session_id=session_id,
        shop_id=data.shop_id,
        customer_message=data.customer_message,
        bot_reply=bot_reply,
        intent=intent,
        status=status,
        handoff_required=handoff_required,
    )

    await _mark_last_simulated(data.shop_id)

    return SimulateOut(
        session_id=session_id,
        shop_id=data.shop_id,
        customer_message=data.customer_message,
        bot_reply=bot_reply,
        intent=intent,
        confidence=confidence,
        handoff_required=handoff_required,
        source=source,
        status=status,
        response_ms=response_ms,
    )


async def _ensure_session(
    session_id: str,
    shop_id: str,
    customer_name: str,
    customer_phone: Optional[str],
    now: str,
):
    existing = await db.sessions.find_one({"session_id": session_id, "shop_id": shop_id})

    if existing:
        update = {"updated_at": now}
        if customer_name and not existing.get("customer_name"):
            update["customer_name"] = customer_name
        if customer_phone and not existing.get("customer_phone"):
            update["customer_phone"] = customer_phone

        await db.sessions.update_one(
            {"session_id": session_id, "shop_id": shop_id},
            {"$set": update},
        )
        return

    await db.sessions.insert_one({
        "session_id": session_id,
        "shop_id": shop_id,
        "source": "simulator",
        "customer_phone": customer_phone,
        "customer_name": customer_name,
        "status": "open",
        "last_intent": None,
        "last_message": None,
        "last_reply": None,
        "message_count": 0,
        "handoff_required": False,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
    })


async def _insert_message(
    session_id: str,
    shop_id: str,
    role: str,
    channel: str,
    text: str,
    intent: Optional[str],
    confidence: Optional[str],
    source: str,
    metadata: dict,
):
    now = now_iso()

    await db.messages.insert_one({
        "message_id": new_id("msg"),
        "session_id": session_id,
        "shop_id": shop_id,
        "role": role,
        "channel": channel,
        "text": text,
        "intent": intent,
        "confidence": confidence,
        "source": source,
        "metadata": metadata or {},
        "created_at": now,
    })


async def _update_session_after_reply(
    session_id: str,
    shop_id: str,
    customer_message: str,
    bot_reply: str,
    intent: str,
    status: str,
    handoff_required: bool,
):
    now = now_iso()

    await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop_id},
        {
            "$set": {
                "status": status,
                "last_intent": intent,
                "last_message": customer_message,
                "last_reply": bot_reply,
                "handoff_required": handoff_required,
                "updated_at": now,
            },
            "$inc": {"message_count": 2},
        },
    )


async def _build_recent_history(session_id: str, shop_id: str) -> str:
    messages = await db.messages.find(
        {"session_id": session_id, "shop_id": shop_id},
        {"_id": 0, "role": 1, "text": 1},
    ).sort("created_at", -1).limit(8).to_list(8)

    messages = list(reversed(messages))
    lines = []

    for msg in messages:
        role = msg.get("role")
        text = msg.get("text") or ""

        if not text:
            continue

        if role == "customer":
            lines.append(f"Pelanggan: {text}")
        elif role == "bot":
            lines.append(f"Bot: {text}")

    return "\n".join(lines) if lines else "-"


async def _mark_last_simulated(shop_id: str):
    now = now_iso()

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {
            "$set": {
                "last_simulated_at": now,
                "updated_at": now,
            },
            "$setOnInsert": {
                "shop_id": shop_id,
                "created_at": now,
            },
        },
        upsert=True,
    )


def _find_faq_match(message: str, faqs: list) -> str:
    msg_lower = message.lower().strip()
    best_score = 0
    best_answer = ""

    for faq in faqs:
        q = (faq.get("question") or "").lower()
        if not q:
            continue

        if q in msg_lower or msg_lower in q:
            return faq.get("answer", "")

        q_tokens = set(q.split())
        msg_tokens = set(msg_lower.split())
        overlap = len(q_tokens & msg_tokens)
        score = overlap / max(len(q_tokens), 1)

        if score >= 0.6 and score > best_score:
            best_score = score
            best_answer = faq.get("answer", "")

    return best_answer


def _guess_intent(message: str) -> str:
    msg = message.lower()

    if any(w in msg for w in ["harga", "berapa", "price"]):
        return "price_inquiry"
    if any(w in msg for w in ["ada", "stok", "tersedia", "habis"]):
        return "stock_inquiry"
    if any(w in msg for w in ["jam", "buka", "tutup"]):
        return "hours_inquiry"
    if any(w in msg for w in ["bayar", "transfer", "qris", "rekening"]):
        return "payment_inquiry"
    if any(w in msg for w in ["delivery", "antar", "ongkir", "kirim"]):
        return "delivery_inquiry"
    if any(w in msg for w in ["lokasi", "alamat", "dimana"]):
        return "location_inquiry"
    if any(w in msg for w in ["pesan", "order", "beli", "mau"]):
        return "order_intent"
    if any(w in msg for w in ["promo", "diskon", "sale"]):
        return "promo_inquiry"

    return "general_inquiry"


def _detect_handoff_in_reply(reply: str) -> bool:
    phrases = [
        "hubungi admin",
        "bicara dengan admin",
        "langsung hubungi",
        "teruskan ke owner",
        "saya teruskan",
        "aku teruskan",
        "hubungkan ke owner",
        "hubungkan ke admin",
    ]

    return any(p in reply.lower() for p in phrases)
