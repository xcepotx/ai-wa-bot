"""
Simulator route — test bot tanpa WhatsApp real.
POST /api/simulate
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from context_service import get_shop_context
from prompt_builder import build_system_prompt, build_handoff_check_prompt
from llm_service import chat_text

logger = logging.getLogger("ai-wa-bot")
router = APIRouter()


class SimulateIn(BaseModel):
    shop_id: str
    customer_message: str
    customer_name: Optional[str] = "Pelanggan"
    session_id: Optional[str] = None   # opsional, untuk multi-turn


class SimulateOut(BaseModel):
    session_id: str
    shop_id: str
    customer_message: str
    bot_reply: str
    intent: str
    confidence: str         # high | medium | low
    handoff_required: bool
    source: str             # llm | faq | fallback
    response_ms: int


@router.post("/simulate", response_model=SimulateOut)
async def simulate(data: SimulateIn):
    """
    Test bot response tanpa WhatsApp real.
    Cocok untuk owner preview sebelum aktifkan auto-reply.
    """
    import time
    t0 = time.time()

    # 1. Fetch context dari Lapakin
    context = await get_shop_context(data.shop_id)
    if not context:
        raise HTTPException(
            status_code=404,
            detail=f"Context toko {data.shop_id} tidak ditemukan. Pastikan shop_id benar dan bot context API Lapakin aktif."
        )

    bot_settings  = context.get("bot_settings", {})
    handoff_kw    = bot_settings.get("handoff_keywords", [])
    fallback_msg  = bot_settings.get("fallback_message", "Maaf kak, silakan hubungi admin kami ya 🙏")

    # 2. Cek handoff keyword dulu (cepat, tidak perlu LLM)
    msg_lower = data.customer_message.lower()
    for kw in handoff_kw:
        if kw.lower() in msg_lower:
            return SimulateOut(
                session_id   = data.session_id or f"sim_{uuid.uuid4().hex[:8]}",
                shop_id      = data.shop_id,
                customer_message = data.customer_message,
                bot_reply    = fallback_msg,
                intent       = "handoff_request",
                confidence   = "high",
                handoff_required = True,
                source       = "keyword_match",
                response_ms  = int((time.time() - t0) * 1000),
            )

    # 3. Cek apakah ada FAQ yang cocok (exact/substring match dulu)
    faq_reply = _find_faq_match(data.customer_message, context.get("faqs", []))
    if faq_reply:
        return SimulateOut(
            session_id   = data.session_id or f"sim_{uuid.uuid4().hex[:8]}",
            shop_id      = data.shop_id,
            customer_message = data.customer_message,
            bot_reply    = faq_reply,
            intent       = "faq_match",
            confidence   = "high",
            handoff_required = False,
            source       = "faq",
            response_ms  = int((time.time() - t0) * 1000),
        )

    # 4. Build system prompt dari context
    system_prompt = build_system_prompt(context)

    # 5. Kirim ke LLM
    shop_name = context.get("shop", {}).get("name", "toko")
    user_msg  = f"Pelanggan ({data.customer_name}): {data.customer_message}"

    bot_reply = await chat_text(system_prompt, user_msg)

    if not bot_reply:
        bot_reply = fallback_msg
        source    = "fallback"
        intent    = "unknown"
        confidence = "low"
    else:
        source    = "llm"
        intent    = _guess_intent(data.customer_message)
        confidence = "medium"

    # 6. Deteksi handoff dari reply LLM
    handoff_required = _detect_handoff_in_reply(bot_reply, handoff_kw)

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
    """
    Simple FAQ matching — cari question yang paling mirip.
    Fuzzy sederhana: cek kata-kata kunci dari question ada di message.
    """
    msg_lower = message.lower().strip()
    best_score = 0
    best_answer = ""

    for faq in faqs:
        q = (faq.get("question") or "").lower()
        if not q:
            continue

        # Exact match
        if q in msg_lower or msg_lower in q:
            return faq.get("answer", "")

        # Token overlap score
        q_tokens   = set(q.split())
        msg_tokens = set(msg_lower.split())
        overlap    = len(q_tokens & msg_tokens)
        score      = overlap / max(len(q_tokens), 1)

        if score >= 0.6 and score > best_score:
            best_score  = score
            best_answer = faq.get("answer", "")

    return best_answer


def _guess_intent(message: str) -> str:
    """Tebak intent dari kata kunci sederhana."""
    msg = message.lower()
    if any(w in msg for w in ["harga", "berapa", "price", "cost"]):
        return "price_inquiry"
    if any(w in msg for w in ["ada", "stock", "stok", "tersedia", "habis"]):
        return "stock_inquiry"
    if any(w in msg for w in ["jam", "buka", "tutup", "operasional"]):
        return "hours_inquiry"
    if any(w in msg for w in ["bayar", "transfer", "qris", "rekening", "dp"]):
        return "payment_inquiry"
    if any(w in msg for w in ["delivery", "antar", "ongkir", "kirim"]):
        return "delivery_inquiry"
    if any(w in msg for w in ["lokasi", "alamat", "dimana", "maps"]):
        return "location_inquiry"
    if any(w in msg for w in ["pesan", "order", "beli", "mau"]):
        return "order_intent"
    if any(w in msg for w in ["promo", "diskon", "sale", "murah"]):
        return "promo_inquiry"
    return "general_inquiry"


def _detect_handoff_in_reply(reply: str, handoff_kw: list) -> bool:
    """Deteksi apakah bot reply-nya sendiri minta handoff ke manusia."""
    handoff_phrases = ["hubungi admin", "bicara dengan admin", "admin kami", "langsung hubungi"]
    reply_lower = reply.lower()
    return any(p in reply_lower for p in handoff_phrases)
