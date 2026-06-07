"""Lightweight webchat lead capture + owner notification."""
import os
import re
from typing import Any, Dict, Optional

from deps import db, now_iso, new_id
from routes.provider_waha import _send_waha_text


PHONE_RE = re.compile(r"(?:(?:\+?62)|0)\s?[-\s]?\d(?:[-\s]?\d){7,14}")


ORDER_KEYWORDS = [
    "mau pesan", "pesan", "order", "beli", "checkout", "minat", "tertarik",
    "mau ini", "ambil", "booking", "dp", "custom", "request", "buatkan",
    "bisa buat", "bisa bikin", "estimasi", "quote", "quotation",
    "superman", "anime", "figure", "figur", "model 3d", "3d print",
]


def _clean_text(value: Any, limit: int = 1200) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def normalize_wa_number(raw: str) -> str:
    text = re.sub(r"[^\d+]", "", raw or "")
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("0"):
        text = "62" + text[1:]
    return text


def wa_chat_id(phone: str) -> str:
    phone = normalize_wa_number(phone)
    if phone.endswith("@c.us") or phone.endswith("@s.whatsapp.net"):
        return phone
    return f"{phone}@c.us"


def extract_phone(message: str) -> Optional[str]:
    match = PHONE_RE.search(message or "")
    if not match:
        return None
    normalized = normalize_wa_number(match.group(0))
    if len(normalized) < 10:
        return None
    return normalized


def extract_name(message: str, phone: Optional[str]) -> Optional[str]:
    text = message or ""
    if phone:
        text = text.replace(phone, " ")
        text = text.replace("+" + phone, " ")

    patterns = [
        r"(?:nama saya|saya|nama|aku)\s+([A-Za-zÀ-ÿ'\.\s]{2,40})",
        r"^([A-Za-zÀ-ÿ'\.\s]{2,40})\s+(?:0|62|\+62)\d",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            name = re.sub(r"\s+", " ", m.group(1)).strip(" .,-")
            if 2 <= len(name) <= 40:
                return name

    return None


def should_request_contact(message: str, sim_result: Dict[str, Any]) -> bool:
    lower = (message or "").lower()
    intent = (sim_result.get("intent") or "").lower()
    source = (sim_result.get("source") or "").lower()
    confidence = (sim_result.get("confidence") or "").lower()

    strong_contact_or_order = any(k in lower for k in [
        "mau pesan", "pesan sekarang", "order sekarang", "checkout", "beli sekarang",
        "ambil", "dp", "booking", "lanjut order", "lanjut pesan",
        "nomor saya", "wa saya", "whatsapp saya", "ini nomor", "hubungi saya",
    ])

    # For early custom consultation from the recommendation engine, do not ask for contact too soon.
    # Let the customer answer reference/idea/brief first, otherwise the reply feels like two questions at once.
    if intent == "custom_request" and source == "rule_recommendation" and not strong_contact_or_order:
        return False

    if any(k in lower for k in ORDER_KEYWORDS):
        return True

    if intent in {"order_intent", "product_inquiry", "custom_request", "general_inquiry"} and source == "llm":
        return True

    if confidence in {"low", "medium"} and source in {"llm", "fallback"}:
        return True

    return False


async def _conversation_summary(session_id: str, shop_id: str, latest_message: str) -> str:
    rows = await db.messages.find(
        {"session_id": session_id, "shop_id": shop_id},
        {"_id": 0, "role": 1, "text": 1, "created_at": 1},
    ).sort("created_at", -1).limit(8).to_list(8)

    rows = list(reversed(rows))
    parts = []
    for row in rows:
        role = row.get("role") or "unknown"
        text = (row.get("text") or "").strip()
        if text:
            parts.append(f"{role}: {text[:240]}")

    if latest_message and (not parts or latest_message not in parts[-1]):
        parts.append(f"customer: {latest_message[:240]}")

    return "\n".join(parts)[-1800:]



def _strip_phone_from_text(text: str) -> str:
    cleaned = PHONE_RE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")
    return cleaned


def _extract_first_match(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, text or "", re.IGNORECASE)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip(" .,-")


def _brief_notes_text(custom_brief: Dict[str, Any]) -> str:
    notes = custom_brief.get("notes") or []
    if not isinstance(notes, list):
        return ""

    cleaned = []
    for note in notes:
        text = _strip_phone_from_text(str(note or ""))
        if text:
            cleaned.append(text)

    return " | ".join(cleaned[-6:])


def _build_custom_brief_summary(custom_brief: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(custom_brief, dict) or not custom_brief:
        return None

    notes_text = _brief_notes_text(custom_brief)
    if not notes_text:
        return None

    idea = _extract_first_match(
        r"(?:mau|buat|bikin|request|custom)\s+(.+?)(?:\s+(?:tinggi|ukuran|panjang|lebar|sekitar|\d+\s?(?:cm|mm)|\d+\s?(?:pcs|pc|buah|unit|set)|warna|deadline|tgl|tanggal)|$)",
        notes_text,
    )
    if not idea:
        idea = _extract_first_match(r"(karakter\s+[^|,.;]+)", notes_text)

    if idea:
        idea = re.sub(r"\s*\|.*$", "", idea).strip(" .,-|")

    size = _extract_first_match(
        r"((?:tinggi|ukuran|panjang|lebar)?\s*(?:sekitar\s*)?\d+(?:[.,]\d+)?\s?(?:cm|mm|meter|m))",
        notes_text,
    )
    qty = _extract_first_match(r"(\d+\s?(?:pcs|pc|buah|unit|biji|set))", notes_text)
    color = _extract_first_match(r"(?:warna|finishing)\s+([A-Za-zÀ-ÿ0-9\s\-]{3,40})", notes_text)
    deadline = _extract_first_match(r"(?:deadline|butuh|sebelum|tanggal|tgl)\s+([^|,.;]+)", notes_text)

    if color:
        color = re.sub(r"\b(deadline|butuh|sebelum|tanggal|tgl).*$", "", color, flags=re.IGNORECASE).strip(" .,-")
    if deadline:
        deadline = deadline.strip(" .,-")

    lines = []
    if idea:
        lines.append(f"- Kebutuhan: {idea}")
    elif custom_brief.get("has_idea"):
        lines.append("- Kebutuhan: custom karakter/model")

    if size:
        lines.append(f"- Ukuran: {size}")
    if qty:
        lines.append(f"- Jumlah: {qty}")
    if color:
        lines.append(f"- Warna/finishing: {color}")
    if deadline:
        lines.append(f"- Deadline: {deadline}")

    if not lines:
        return None

    return "\n".join(lines)


def _build_customer_capture_reply(custom_summary: Optional[str]) -> str:
    if custom_summary:
        return (
            "Terima kasih kak. Nomor WhatsApp sudah saya terima.\n\n"
            "Saya rangkum brief awalnya ya:\n"
            f"{custom_summary}\n\n"
            "Admin SpaceCraft akan follow up untuk cek estimasi harga dan waktu pengerjaan."
        )

    return (
        "Terima kasih kak. Nomor WhatsApp sudah kami terima. "
        "Admin SpaceCraft akan follow up untuk bantu cek kebutuhan dan estimasi harganya ya."
    )


def _build_owner_need_summary(message: str, custom_summary: Optional[str]) -> str:
    if custom_summary:
        return "Brief custom:\n" + custom_summary
    return _clean_text(message, 1000) or "-"


async def _notify_owner(lead: Dict[str, Any]) -> Dict[str, Any]:
    enabled = os.environ.get("WEBCHAT_LEAD_NOTIFY_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    owner_phone = os.environ.get("SPACECRAFT_OWNER_WA", "").strip()

    if not enabled:
        return {"ok": False, "skipped": True, "reason": "notification_disabled"}
    if not owner_phone:
        return {"ok": False, "skipped": True, "reason": "SPACECRAFT_OWNER_WA_not_set"}

    lead_phone = lead.get("customer_phone") or "-"
    lead_name = lead.get("customer_name") or "Belum disebutkan"
    need = lead.get("need_summary") or lead.get("last_message") or "-"
    page_url = lead.get("page_url") or "-"
    session_id = lead.get("session_id") or "-"

    text = (
        "📩 Lead baru dari Web Chat SpaceCraft\n\n"
        f"Nama: {lead_name}\n"
        f"WA calon pembeli: {lead_phone}\n"
        f"Kebutuhan: {need}\n"
        f"Halaman: {page_url}\n"
        f"Session: {session_id}\n\n"
        "Mohon follow up manual ya."
    )

    return _send_waha_text(wa_chat_id(owner_phone), text)


async def process_webchat_lead(
    *,
    shop_id: str,
    session_id: str,
    customer_name: str,
    customer_phone: str,
    message: str,
    page_url: Optional[str],
    origin: Optional[str],
    sim_result: Dict[str, Any],
) -> Dict[str, Any]:
    now = now_iso()
    phone = extract_phone(message)
    detected_name = extract_name(message, phone)
    request_contact = should_request_contact(message, sim_result)

    existing = await db.webchat_leads.find_one(
        {
            "shop_id": shop_id,
            "session_id": session_id,
            "status": {"$in": ["contact_requested", "new", "notified"]},
        },
        {"_id": 0},
    )

    if phone:
        lead_id = existing.get("lead_id") if existing else new_id("lead")
        final_name = detected_name or (customer_name if customer_name != "Website Visitor" else None)

        summary = await _conversation_summary(session_id, shop_id, message)
        session_doc = await db.sessions.find_one(
            {"session_id": session_id, "shop_id": shop_id},
            {"_id": 0, "custom_brief": 1},
        ) or {}
        custom_summary = _build_custom_brief_summary(session_doc.get("custom_brief"))

        lead_doc = {
            "lead_id": lead_id,
            "shop_id": shop_id,
            "session_id": session_id,
            "source": "webchat",
            "status": "new",
            "customer_name": final_name,
            "customer_phone": phone,
            "customer_phone_chat_id": wa_chat_id(phone),
            "need_summary": _build_owner_need_summary(message, custom_summary),
              "custom_brief_summary": custom_summary,
            "conversation_summary": summary,
            "last_message": _clean_text(message, 1000),
            "page_url": page_url,
            "origin": origin,
            "intent": sim_result.get("intent"),
            "confidence": sim_result.get("confidence"),
            "reply_source": sim_result.get("source"),
            "updated_at": now,
        }

        await db.webchat_leads.update_one(
            {"lead_id": lead_id},
            {
                "$set": lead_doc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

        notify_result = await _notify_owner(lead_doc)
        notification_status = "sent" if notify_result.get("ok") else "failed"

        await db.webchat_leads.update_one(
            {"lead_id": lead_id},
            {
                "$set": {
                    "status": "notified" if notify_result.get("ok") else "new",
                    "owner_notification": notify_result,
                    "owner_notification_status": notification_status,
                    "owner_notified_at": now if notify_result.get("ok") else None,
                    "updated_at": now,
                }
            },
        )

        await db.sessions.update_one(
            {"session_id": session_id, "shop_id": shop_id},
            {
                "$set": {
                    "status": "handoff",
                    "handoff_required": True,
                    "lead_id": lead_id,
                    "lead_capture_state": "captured",
                    "lead_customer_phone": phone,
                    "updated_at": now,
                }
            },
        )

        await db.bot_events.insert_one({
            "event_id": new_id("evt"),
            "shop_id": shop_id,
            "type": "webchat.lead_captured",
            "payload": {
                "lead_id": lead_id,
                "session_id": session_id,
                "customer_phone": phone,
                "notification_status": notification_status,
                "notify_result": notify_result,
            },
            "created_at": now,
        })

        return {
            "lead_id": lead_id,
            "captured": True,
            "notification": notify_result,
            "reply_override": _build_customer_capture_reply(custom_summary),
        }

    if request_contact and not existing:
        lead_id = new_id("lead")
        summary = await _conversation_summary(session_id, shop_id, message)

        await db.webchat_leads.insert_one({
            "lead_id": lead_id,
            "shop_id": shop_id,
            "session_id": session_id,
            "source": "webchat",
            "status": "contact_requested",
            "customer_name": None if customer_name == "Website Visitor" else customer_name,
            "customer_phone": None,
            "need_summary": _clean_text(message, 1000),
            "conversation_summary": summary,
            "last_message": _clean_text(message, 1000),
            "page_url": page_url,
            "origin": origin,
            "intent": sim_result.get("intent"),
            "confidence": sim_result.get("confidence"),
            "reply_source": sim_result.get("source"),
            "created_at": now,
            "updated_at": now,
        })

        await db.sessions.update_one(
            {"session_id": session_id, "shop_id": shop_id},
            {
                "$set": {
                    "lead_id": lead_id,
                    "lead_capture_state": "requested",
                    "updated_at": now,
                }
            },
        )

        await db.bot_events.insert_one({
            "event_id": new_id("evt"),
            "shop_id": shop_id,
            "type": "webchat.lead_contact_requested",
            "payload": {
                "lead_id": lead_id,
                "session_id": session_id,
                "message": message,
            },
            "created_at": now,
        })

        return {
            "lead_id": lead_id,
            "captured": False,
            "contact_requested": True,
            "append_reply": (
                "Biar admin bisa bantu cek estimasi lebih cepat, boleh tinggalkan nama dan nomor WhatsApp kak?"
            ),
        }

    return {"captured": False, "contact_requested": False}
