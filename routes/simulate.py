"""Simulator route — test bot tanpa WhatsApp real."""
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from context_service import get_shop_context, invalidate_cache
from prompt_builder import build_system_prompt
from deps import db, require_user, now_iso

logger = logging.getLogger("ai-wa-bot")
router = APIRouter()


class SimulateIn(BaseModel):
    shop_id: str
    customer_message: str
    customer_name: Optional[str] = "Pelanggan"
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
    response_ms: int


@router.post("/simulate", response_model=SimulateOut)
async def simulate(data: SimulateIn):
    import time
    t0 = time.time()

    # 1. Fetch context
    context = await get_shop_context(data.shop_id)
    if not context:
        raise HTTPException(
            status_code=404,
            detail=f"Toko {data.shop_id} tidak ditemukan."
        )

    bot_settings = context.get("bot_settings", {})
    handoff_kw   = bot_settings.get("handoff_keywords", [])
    fallback_msg = bot_settings.get(
        "fallback_message", "Maaf kak, silakan hubungi admin kami ya 🙏"
    )

    # 2. Cek handoff keyword
    msg_lower = data.customer_message.lower()
    for kw in handoff_kw:
        if kw.lower() in msg_lower:
            return SimulateOut(
                session_id       = data.session_id or f"sim_{uuid.uuid4().hex[:8]}",
                shop_id          = data.shop_id,
                customer_message = data.customer_message,
                bot_reply        = fallback_msg,
                intent           = "handoff_request",
                confidence       = "high",
                handoff_required = True,
                source           = "keyword_match",
                response_ms      = int((time.time() - t0) * 1000),
            )

    # 3. Cek FAQ match
    faq_reply = _find_faq_match(data.customer_message, context.get("faqs", []))
    if faq_reply:
        return SimulateOut(
            session_id       = data.session_id or f"sim_{uuid.uuid4().hex[:8]}",
            shop_id          = data.shop_id,
            customer_message = data.customer_message,
            bot_reply        = faq_reply,
            intent           = "faq_match",
            confidence       = "high",
            handoff_required = False,
            source           = "faq",
            response_ms      = int((time.time() - t0) * 1000),
        )

    # 4. LLM
    system_prompt = build_system_prompt(context)
    user_msg      = f"Pelanggan ({data.customer_name}): {data.customer_message}"

    try:
        from llm_service import chat_text
        bot_reply = await chat_text(system_prompt, user_msg)
        source    = "llm"
        intent    = _guess_intent(data.customer_message)
        confidence = "medium"
    except Exception as e:
        logger.error("LLM error: %s", e)
        bot_reply  = fallback_msg
        source     = "fallback"
        intent     = "unknown"
        confidence = "low"

    handoff_required = _detect_handoff_in_reply(bot_reply)
    session_id = data.session_id or f"sim_{uuid.uuid4().hex[:8]}"

    return SimulateOut(
        session_id       = session_id,
        shop_id          = data.shop_id,
        customer_message = data.customer_message,
        bot_reply        = bot_reply,
        intent           = intent,
        confidence       = confidence,
        handoff_required = handoff_required,
        source           = source,
        response_ms      = int((time.time() - t0) * 1000),
    )


def _find_faq_match(message: str, faqs: list) -> str:
    msg_lower   = message.lower().strip()
    best_score  = 0
    best_answer = ""

    for faq in faqs:
        q = (faq.get("question") or "").lower()
        if not q:
            continue
        if q in msg_lower or msg_lower in q:
            return faq.get("answer", "")
        q_tokens   = set(q.split())
        msg_tokens = set(msg_lower.split())
        overlap    = len(q_tokens & msg_tokens)
        score      = overlap / max(len(q_tokens), 1)
        if score >= 0.6 and score > best_score:
            best_score  = score
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
    phrases = ["hubungi admin", "bicara dengan admin", "langsung hubungi"]
    return any(p in reply.lower() for p in phrases)
