"""Simulator route - test bot without real WhatsApp + persist sessions/messages."""
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from context_service import get_shop_context
from catalog_service import enrich_context_with_effective_products
from shop_status_service import enrich_context_with_shop_status
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
        shop_exists = await db.shops.find_one({"shop_id": data.shop_id}, {"_id": 0, "shop_id": 1})
        if not shop_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Toko {data.shop_id} tidak ditemukan."
            )
        context = {}

    context = await _enrich_context_from_db(data.shop_id, context)
    context = await enrich_context_with_effective_products(data.shop_id, context)
    context = await enrich_context_with_shop_status(data.shop_id, context)

    session_id = data.session_id or f"sim_{uuid.uuid4().hex[:8]}"
    now = now_iso()

    await _ensure_session(
        session_id=session_id,
        shop_id=data.shop_id,
        customer_name=data.customer_name or "Pelanggan",
        customer_phone=data.customer_phone,
        now=now,
    )

    session_doc = await db.sessions.find_one(
        {"session_id": session_id, "shop_id": data.shop_id},
        {"_id": 0},
    ) or {}

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

    bot_settings = await _load_fresh_bot_settings(data.shop_id, context)
    handoff_kw = bot_settings.get("handoff_keywords", [])
    fallback_msg = bot_settings.get(
        "fallback_message", "Maaf kak, silakan hubungi admin kami ya 🙏"
    )

    if isinstance(handoff_kw, str):
        handoff_kw = [x.strip() for x in handoff_kw.split(",") if x.strip()]

    msg_lower = data.customer_message.lower()

    bot_reply = ""
    intent = "general_inquiry"
    confidence = "medium"
    source = "llm"
    handoff_required = False
    status = "bot_replied"
    extra_session_update = {}

    safety_block = await _check_safety_block(data.shop_id, bot_settings)
    if safety_block:
        bot_reply = safety_block["reply"]
        intent = safety_block["intent"]
        confidence = "high"
        source = "safety_policy"
        handoff_required = False
        status = "skipped"
        response_ms = int((time.time() - t0) * 1000)

        await _insert_message(
            session_id=session_id,
            shop_id=data.shop_id,
            role="system",
            channel="simulator",
            text=bot_reply,
            intent=intent,
            confidence=confidence,
            source=source,
            metadata={
                "reason": safety_block.get("reason"),
                "status": safety_block.get("status"),
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
            extra_set={},
        )

        await _write_bot_event(
            shop_id=data.shop_id,
            event_type=safety_block["event_type"],
            payload={
                "session_id": session_id,
                "reason": safety_block.get("reason"),
                "system_status": safety_block.get("status"),
            },
        )

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

    # 1. Hard handoff keyword.
    for kw in handoff_kw:
        if kw and kw.lower() in msg_lower:
            bot_reply = fallback_msg
            intent = "handoff_request"
            confidence = "high"
            source = "keyword_match"
            handoff_required = True
            status = "handoff"
            break

    # 2. FAQ exact/light match.
    if not bot_reply:
        faq_reply = _find_faq_match(data.customer_message, context.get("faqs", []))
        if faq_reply:
            bot_reply = faq_reply
            intent = "faq_match"
            confidence = "high"
            source = "faq"
            handoff_required = False
            status = "bot_replied"

    # 3. Deterministic commerce rules before LLM.
    if not bot_reply:
        try:
            from reply_rules import build_rule_reply

            rule = build_rule_reply(
                data.customer_message,
                context,
                session_doc=session_doc,
            )

            if rule:
                bot_reply = rule["reply"]
                intent = rule.get("intent", "general_inquiry")
                confidence = rule.get("confidence", "high")
                source = rule.get("source", "rule")
                handoff_required = bool(rule.get("handoff_required", False))
                extra_session_update = rule.get("session_update") or {}
                status = "handoff" if handoff_required else "bot_replied"
        except Exception as e:
            logger.exception("Rule reply error: %s", e)

    # 4. LLM fallback.
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

    auto_reply_policy_result = None
    try:
        from safety_policy import evaluate_auto_reply_policy

        auto_reply_policy_result = await evaluate_auto_reply_policy(
            shop_id=data.shop_id,
            session_id=session_id,
            reply_result={
                "intent": intent,
                "confidence": confidence,
                "handoff_required": handoff_required,
            },
            channel="simulator",
            require_provider_ready=True,
            write_event=False,
        )
    except Exception as e:
        logger.warning("Auto-reply policy preview failed: %s", e)

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
            "session_update": extra_session_update,
            "auto_reply_policy": auto_reply_policy_result,
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
        extra_set=extra_session_update,
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
        "current_product": None,
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
    extra_set: Optional[dict] = None,
):
    now = now_iso()

    set_doc = {
        "status": status,
        "last_intent": intent,
        "last_message": customer_message,
        "last_reply": bot_reply,
        "handoff_required": handoff_required,
        "updated_at": now,
    }

    if extra_set:
        set_doc.update(extra_set)

    await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop_id},
        {
            "$set": set_doc,
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


async def _enrich_context_from_db(shop_id: str, context: dict) -> dict:
    """Merge local DB data into context so rule engine always has fresh shop data.

    get_shop_context() may depend on standalone/Lapakin source behavior and may not
    always include products, FAQs, payment info, or operational profile. Simulator and
    provider adapter must still be able to answer from trusted local DB data.
    """
    if not isinstance(context, dict):
        context = {}

    enriched = dict(context)

    shop_doc = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    if shop_doc:
        existing_shop = enriched.get("shop") if isinstance(enriched.get("shop"), dict) else {}
        enriched["shop"] = {**shop_doc, **existing_shop}

        if not enriched.get("shop_name"):
            enriched["shop_name"] = shop_doc.get("name")

        if not enriched.get("whatsapp"):
            enriched["whatsapp"] = shop_doc.get("whatsapp") or shop_doc.get("whatsapp_number")

    # Products: always fallback to db.products when context does not include products.
    products = enriched.get("products")
    if not isinstance(products, list) or len(products) == 0:
        db_products = await db.products.find(
            {"shop_id": shop_id},
            {"_id": 0},
        ).to_list(300)

        if db_products:
            enriched["products"] = db_products

    # FAQs: fallback to bot_faqs.
    faqs = enriched.get("faqs")
    if not isinstance(faqs, list) or len(faqs) == 0:
        db_faqs = await db.bot_faqs.find(
            {"shop_id": shop_id, "enabled": {"$ne": False}},
            {"_id": 0},
        ).to_list(100)

        if db_faqs:
            enriched["faqs"] = db_faqs

    # Payment info.
    payment_doc = await db.payment_info.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    if payment_doc:
        enriched["payment_info"] = payment_doc

        payment_text = (
            payment_doc.get("instruction")
            or payment_doc.get("payment_instruction")
            or payment_doc.get("description")
        )

        if payment_text and not enriched.get("payment_instruction"):
            enriched["payment_instruction"] = payment_text

    # Operational profile.
    profile_doc = await db.bot_shop_profile.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    if profile_doc:
        enriched["bot_profile"] = profile_doc

        if profile_doc.get("business_hours") and not enriched.get("business_hours"):
            enriched["business_hours"] = profile_doc.get("business_hours")

        if profile_doc.get("address") and not enriched.get("address"):
            enriched["address"] = profile_doc.get("address")

    return enriched


async def _load_fresh_bot_settings(shop_id: str, context: dict) -> dict:
    """Load bot settings directly from DB so admin safety changes are never stale.

    get_shop_context() can include cached or external context. Safety controls like
    admin_disabled must always read the latest local bot_settings document.
    """
    context_settings = context.get("bot_settings", {})
    if not isinstance(context_settings, dict):
        context_settings = {}

    db_settings = await db.bot_settings.find_one(
        {"shop_id": shop_id},
        {"_id": 0},
    ) or {}

    # DB settings must override context settings because admin safety controls
    # are written directly to db.bot_settings.
    merged = {
        **context_settings,
        **db_settings,
    }

    return merged


async def _check_safety_block(shop_id: str, bot_settings: dict) -> Optional[dict]:
    control = await db.system_settings.find_one(
        {"key": "lapakin_asisten_control"},
        {"_id": 0},
    ) or {}

    status = control.get("status", "on")

    if status != "on":
        reason = control.get("reason") or "Sistem sedang tidak aktif."
        label = "maintenance" if status == "maintenance" else "dinonaktifkan"

        return {
            "intent": "system_disabled",
            "event_type": "reply.skipped_global_disabled",
            "status": status,
            "reason": reason,
            "reply": (
                f"Lapakin Asisten sedang {label} sementara oleh admin. "
                f"Alasan: {reason}"
            ),
        }

    if bot_settings.get("admin_disabled"):
        reason = bot_settings.get("admin_disable_reason") or "Dinonaktifkan admin."

        return {
            "intent": "shop_admin_disabled",
            "event_type": "reply.skipped_shop_disabled",
            "status": "shop_disabled",
            "reason": reason,
            "reply": (
                "Lapakin Asisten untuk toko ini sedang dinonaktifkan oleh admin. "
                f"Alasan: {reason}"
            ),
        }

    return None


async def _write_bot_event(shop_id: str, event_type: str, payload: dict):
    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": event_type,
        "payload": payload or {},
        "created_at": now_iso(),
    })


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
