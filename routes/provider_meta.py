"""Meta Cloud API webhook verification + inbound parser.

Current scope:
- GET verification endpoint
- POST inbound parser for text / interactive / button / basic non-text placeholders
- Status webhook logging
- Reply generation through existing Lapakin Asisten engine
- Auto-reply safety policy evaluation
- Store allowed replies as pending_send, not sent yet

Outbound real send will be implemented in the next step.
"""
import os
import uuid
from typing import Optional, Tuple, Dict, Any, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from deps import db, now_iso, new_id, require_admin
from routes.simulate import simulate, SimulateIn
from safety_policy import evaluate_auto_reply_policy

router = APIRouter()


class MetaWebhookTestIn(BaseModel):
    verify_token: str
    challenge: str = "lapakin_asisten_challenge_test"


async def _write_event(shop_id: Optional[str], event_type: str, payload: dict):
    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": event_type,
        "payload": payload or {},
        "created_at": now_iso(),
    })


async def _find_matching_verify_token(token: str) -> Tuple[bool, Optional[dict], str]:
    if not token:
        return False, None, "missing_token"

    env_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "").strip()
    if env_token and token == env_token:
        return True, None, "env"

    cred = await db.provider_credentials.find_one(
        {
            "provider": "meta_cloud",
            "verify_token": token,
            "status": {"$ne": "disabled"},
        },
        {"_id": 0},
    )

    if cred:
        return True, cred, "provider_credentials"

    return False, None, "not_found"


async def _find_credential_by_phone_number(phone_number_id: Optional[str], waba_id: Optional[str]) -> Optional[dict]:
    if phone_number_id:
        cred = await db.provider_credentials.find_one(
            {
                "provider": "meta_cloud",
                "phone_number_id": str(phone_number_id),
                "status": {"$ne": "disabled"},
            },
            {"_id": 0},
        )
        if cred:
            return cred

    if waba_id:
        cred = await db.provider_credentials.find_one(
            {
                "provider": "meta_cloud",
                "waba_id": str(waba_id),
                "status": {"$ne": "disabled"},
            },
            {"_id": 0},
        )
        if cred:
            return cred

    return None


def _normalize_customer_phone(value: Optional[str]) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    text = text.replace(" ", "").replace("-", "")
    if text.startswith("+"):
        return text
    if text.startswith("0"):
        return "+62" + text[1:]
    return "+" + text


def _contact_name(value: dict, fallback: str = "Pelanggan WhatsApp") -> str:
    contacts = value.get("contacts") or []
    if contacts and isinstance(contacts, list):
        profile = contacts[0].get("profile") or {}
        name = profile.get("name")
        if name:
            return str(name)
    return fallback


def _extract_message_text(message: dict) -> Tuple[str, str, bool]:
    """Return text, message_type, supported_text.

    supported_text=False means we can parse enough to create a handoff-like note,
    but not enough for fully automated text understanding.
    """
    msg_type = message.get("type") or "unknown"

    if msg_type == "text":
        text = ((message.get("text") or {}).get("body") or "").strip()
        return text, msg_type, True

    if msg_type == "interactive":
        interactive = message.get("interactive") or {}
        i_type = interactive.get("type")

        if i_type == "button_reply":
            br = interactive.get("button_reply") or {}
            text = br.get("title") or br.get("id") or ""
            return str(text).strip(), msg_type, True

        if i_type == "list_reply":
            lr = interactive.get("list_reply") or {}
            text = lr.get("title") or lr.get("id") or ""
            return str(text).strip(), msg_type, True

        return f"Pelanggan mengirim interactive message tipe {i_type}.", msg_type, False

    if msg_type == "button":
        button = message.get("button") or {}
        text = button.get("text") or button.get("payload") or ""
        return str(text).strip(), msg_type, True

    if msg_type in {"image", "audio", "voice", "video", "document", "sticker"}:
        return f"Pelanggan mengirim {msg_type}. Mohon dicek owner.", msg_type, False

    if msg_type == "location":
        location = message.get("location") or {}
        name = location.get("name") or "lokasi"
        address = location.get("address") or ""
        return f"Pelanggan mengirim lokasi: {name} {address}".strip(), msg_type, False

    return f"Pelanggan mengirim pesan tipe {msg_type}. Mohon dicek owner.", msg_type, False


async def _find_existing_meta_session(shop_id: str, customer_phone: str) -> Optional[dict]:
    return await db.sessions.find_one(
        {
            "shop_id": shop_id,
            "customer_phone": customer_phone,
            "source": "whatsapp",
            "provider": "meta_cloud",
            "status": {"$ne": "resolved"},
        },
        {"_id": 0},
        sort=[("updated_at", -1)],
    )


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
        "provider": "meta_cloud",
        "channel": "whatsapp",
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
    metadata["provider_meta"] = {
        "provider_status": provider_status,
        "policy": policy_result,
        "updated_at": now_iso(),
    }

    await db.messages.update_one(
        {"message_id": msg["message_id"]},
        {"$set": {"metadata": metadata}},
    )


def _as_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    return dict(value)


async def _process_meta_message(entry_id: Optional[str], value: dict, message: dict) -> dict:
    metadata = value.get("metadata") or {}
    phone_number_id = metadata.get("phone_number_id")
    display_phone_number = metadata.get("display_phone_number")
    waba_id = entry_id

    credential = await _find_credential_by_phone_number(phone_number_id, waba_id)

    if not credential:
        await _write_event(
            None,
            "provider.meta.unmatched_phone_number",
            {
                "phone_number_id": phone_number_id,
                "display_phone_number": display_phone_number,
                "waba_id": waba_id,
                "message_id": message.get("id"),
            },
        )
        return {
            "ok": False,
            "ignored": True,
            "reason": "provider credential not found for phone_number_id/waba_id",
            "phone_number_id": phone_number_id,
            "message_id": message.get("id"),
        }

    shop_id = credential.get("shop_id")
    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0}) or {}

    if not shop:
        await _write_event(
            shop_id,
            "provider.meta.shop_not_found",
            {
                "phone_number_id": phone_number_id,
                "waba_id": waba_id,
                "message_id": message.get("id"),
            },
        )
        return {
            "ok": False,
            "ignored": True,
            "reason": "shop not found for credential",
            "shop_id": shop_id,
            "message_id": message.get("id"),
        }

    provider_message_id = message.get("id") or f"meta_in_{uuid.uuid4().hex[:16]}"
    customer_phone = _normalize_customer_phone(message.get("from"))
    customer_name = _contact_name(value)

    duplicate = await db.provider_messages.find_one(
        {
            "provider": "meta_cloud",
            "direction": "inbound",
            "provider_message_id": provider_message_id,
            "shop_id": shop_id,
        },
        {"_id": 0},
    )

    if duplicate:
        return {
            "ok": True,
            "duplicate": True,
            "shop_id": shop_id,
            "session_id": duplicate.get("session_id"),
            "provider_message_id": provider_message_id,
            "status": duplicate.get("status"),
        }

    message_text, message_type, supported_text = _extract_message_text(message)

    if not message_text:
        message_text = f"Pelanggan mengirim pesan kosong/unsupported tipe {message_type}. Mohon dicek owner."
        supported_text = False

    existing_session = await _find_existing_meta_session(shop_id, customer_phone)

    sim_input = SimulateIn(
        shop_id=shop_id,
        session_id=existing_session.get("session_id") if existing_session else None,
        customer_message=message_text,
        customer_name=customer_name,
        customer_phone=customer_phone,
    )

    sim_result = _as_dict(await simulate(sim_input))
    session_id = sim_result["session_id"]

    await db.sessions.update_one(
        {"session_id": session_id, "shop_id": shop_id},
        {
            "$set": {
                "source": "whatsapp",
                "provider": "meta_cloud",
                "provider_customer_phone": customer_phone,
                "last_provider_message_id": provider_message_id,
                "meta_phone_number_id": phone_number_id,
                "meta_display_phone_number": display_phone_number,
                "updated_at": now_iso(),
            }
        },
    )

    await db.messages.update_many(
        {"session_id": session_id, "shop_id": shop_id, "channel": "simulator"},
        {"$set": {"channel": "whatsapp"}},
    )

    await _store_provider_message(
        shop_id=shop_id,
        session_id=session_id,
        direction="inbound",
        provider_message_id=provider_message_id,
        customer_phone=customer_phone,
        customer_name=customer_name,
        text=message_text,
        status="received",
        payload={
            "raw_message": message,
            "message_type": message_type,
            "supported_text": supported_text,
            "phone_number_id": phone_number_id,
            "display_phone_number": display_phone_number,
            "waba_id": waba_id,
        },
    )

    await _write_event(
        shop_id,
        "provider.meta.message_received",
        {
            "session_id": session_id,
            "provider_message_id": provider_message_id,
            "customer_phone": customer_phone,
            "message_type": message_type,
            "supported_text": supported_text,
            "text": message_text,
        },
    )

    if not supported_text:
        policy_result = {
            "allowed": False,
            "action": "handoff_required",
            "status": "handoff",
            "reason": f"Message type {message_type} belum bisa diproses otomatis.",
            "code": "UNSUPPORTED_MESSAGE_TYPE",
            "channel": "whatsapp",
        }
    elif sim_result.get("source") == "safety_policy" or sim_result.get("status") == "skipped":
        policy_result = {
            "allowed": False,
            "action": "skipped_by_reply_engine",
            "status": "skipped",
            "reason": sim_result.get("bot_reply"),
            "code": sim_result.get("intent"),
            "channel": "whatsapp",
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
            channel="whatsapp",
            require_provider_ready=True,
            write_event=True,
        )

    if policy_result.get("allowed"):
        provider_status = "pending_send"
        session_status = "bot_replied"
        event_type = "provider.meta.reply_ready"
    else:
        action = policy_result.get("action")
        if action == "handoff_required":
            provider_status = "handoff"
            session_status = "handoff"
            event_type = "provider.meta.reply_handoff"
        elif action == "draft_only":
            provider_status = "draft_only"
            session_status = "draft_only"
            event_type = "provider.meta.reply_draft_only"
        else:
            provider_status = "skipped"
            session_status = "skipped"
            event_type = "provider.meta.reply_skipped"

    outbound_provider_message_id = f"meta_pending_{uuid.uuid4().hex[:16]}"

    await _store_provider_message(
        shop_id=shop_id,
        session_id=session_id,
        direction="outbound",
        provider_message_id=outbound_provider_message_id,
        customer_phone=customer_phone,
        customer_name=customer_name,
        text=sim_result.get("bot_reply") or "",
        status=provider_status,
        payload={
            "policy": policy_result,
            "reply_result": sim_result,
            "send_note": "pending_send means reply is approved but not sent to Meta yet",
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

    await _write_event(
        shop_id,
        event_type,
        {
            "session_id": session_id,
            "inbound_provider_message_id": provider_message_id,
            "outbound_provider_message_id": outbound_provider_message_id,
            "provider_status": provider_status,
            "policy": policy_result,
            "message_type": message_type,
        },
    )

    return {
        "ok": True,
        "duplicate": False,
        "shop_id": shop_id,
        "shop_name": shop.get("name"),
        "session_id": session_id,
        "customer_phone": customer_phone,
        "customer_name": customer_name,
        "inbound_provider_message_id": provider_message_id,
        "outbound_provider_message_id": outbound_provider_message_id,
        "message_type": message_type,
        "supported_text": supported_text,
        "customer_message": message_text,
        "bot_reply": sim_result.get("bot_reply"),
        "reply_source": sim_result.get("source"),
        "intent": sim_result.get("intent"),
        "confidence": sim_result.get("confidence"),
        "provider_status": provider_status,
        "policy": policy_result,
    }


async def _process_meta_status(entry_id: Optional[str], value: dict, status_obj: dict) -> dict:
    metadata = value.get("metadata") or {}
    phone_number_id = metadata.get("phone_number_id")
    credential = await _find_credential_by_phone_number(phone_number_id, entry_id)

    shop_id = credential.get("shop_id") if credential else None

    payload = {
        "waba_id": entry_id,
        "phone_number_id": phone_number_id,
        "status_id": status_obj.get("id"),
        "status": status_obj.get("status"),
        "recipient_id": status_obj.get("recipient_id"),
        "timestamp": status_obj.get("timestamp"),
        "conversation": status_obj.get("conversation"),
        "pricing": status_obj.get("pricing"),
        "errors": status_obj.get("errors"),
    }

    await _write_event(shop_id, "provider.meta.status_received", payload)

    # If later outbound sender stores Meta's real wamid as provider_message_id,
    # this update will keep provider_messages in sync.
    if shop_id and status_obj.get("id"):
        await db.provider_messages.update_one(
            {
                "provider": "meta_cloud",
                "shop_id": shop_id,
                "provider_message_id": status_obj.get("id"),
            },
            {
                "$set": {
                    "status": status_obj.get("status"),
                    "status_payload": status_obj,
                    "updated_at": now_iso(),
                }
            },
        )

    return {
        "ok": True,
        "shop_id": shop_id,
        "status_id": status_obj.get("id"),
        "status": status_obj.get("status"),
    }


@router.get("/provider/meta/webhook")
async def meta_webhook_verify(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    verify_token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    ok, credential, token_source = await _find_matching_verify_token(verify_token or "")
    shop_id = credential.get("shop_id") if credential else None

    if mode == "subscribe" and ok and challenge is not None:
        await _write_event(
            shop_id,
            "provider.meta.webhook_verified",
            {
                "mode": mode,
                "token_source": token_source,
                "shop_id": shop_id,
                "challenge_preview": str(challenge)[:12],
                "remote": request.client.host if request.client else None,
            },
        )
        return PlainTextResponse(str(challenge), status_code=200)

    await _write_event(
        shop_id,
        "provider.meta.webhook_verify_failed",
        {
            "mode": mode,
            "token_source": token_source,
            "has_challenge": challenge is not None,
            "shop_id": shop_id,
            "remote": request.client.host if request.client else None,
        },
    )

    raise HTTPException(status_code=403, detail="Webhook verification failed")


@router.post("/provider/meta/webhook")
async def meta_webhook_receive(request: Request):
    """Parse real Meta webhook payload.

    Returns 200 quickly so Meta does not retry. Per-message errors are collected
    in the response and bot_events.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    object_type = body.get("object")
    entries = body.get("entry") or []

    processed_messages: List[dict] = []
    processed_statuses: List[dict] = []
    ignored: List[dict] = []
    errors: List[dict] = []

    await _write_event(
        None,
        "provider.meta.webhook_received",
        {
            "object": object_type,
            "entries_count": len(entries) if isinstance(entries, list) else 0,
        },
    )

    if object_type != "whatsapp_business_account" or not isinstance(entries, list):
        await _write_event(
            None,
            "provider.meta.webhook_ignored",
            {
                "reason": "invalid object or entry",
                "object": object_type,
            },
        )
        return {
            "ok": True,
            "received": True,
            "ignored": True,
            "reason": "invalid object or entry",
        }

    for entry in entries:
        entry_id = entry.get("id")
        changes = entry.get("changes") or []

        if not isinstance(changes, list):
            ignored.append({"entry_id": entry_id, "reason": "changes not list"})
            continue

        for change in changes:
            field = change.get("field")
            value = change.get("value") or {}

            if field != "messages":
                ignored.append({"entry_id": entry_id, "field": field, "reason": "unsupported field"})
                continue

            for message in value.get("messages") or []:
                try:
                    processed_messages.append(await _process_meta_message(entry_id, value, message))
                except Exception as e:
                    error = {
                        "entry_id": entry_id,
                        "message_id": message.get("id"),
                        "error": str(e),
                    }
                    errors.append(error)
                    await _write_event(None, "provider.meta.message_parse_error", error)

            for status_obj in value.get("statuses") or []:
                try:
                    processed_statuses.append(await _process_meta_status(entry_id, value, status_obj))
                except Exception as e:
                    error = {
                        "entry_id": entry_id,
                        "status_id": status_obj.get("id"),
                        "error": str(e),
                    }
                    errors.append(error)
                    await _write_event(None, "provider.meta.status_parse_error", error)

    return {
        "ok": True,
        "received": True,
        "parser": "real_inbound_dry_run",
        "processed_messages": processed_messages,
        "processed_statuses": processed_statuses,
        "ignored": ignored,
        "errors": errors,
        "counts": {
            "messages": len(processed_messages),
            "statuses": len(processed_statuses),
            "ignored": len(ignored),
            "errors": len(errors),
        },
    }


@router.post("/admin/provider/meta/webhook-test")
async def admin_meta_webhook_test(data: MetaWebhookTestIn, request: Request):
    await require_admin(request)

    ok, credential, token_source = await _find_matching_verify_token(data.verify_token)

    result = {
        "ok": ok,
        "token_source": token_source,
        "challenge": data.challenge if ok else None,
        "shop_id": credential.get("shop_id") if credential else None,
        "provider": credential.get("provider") if credential else None,
    }

    await _write_event(
        result["shop_id"],
        "provider.meta.webhook_tested",
        {
            "ok": ok,
            "token_source": token_source,
            "shop_id": result["shop_id"],
        },
    )

    return result
