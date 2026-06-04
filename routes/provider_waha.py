"""WAHA webhook adapter.

This adapter receives WAHA Core/Plus webhook payloads and reuses the existing
provider mock pipeline as the internal message processor. It only sends a real
WAHA reply when the existing safety policy marks the reply as allowed.
"""

import json
import os
import urllib.error
import urllib.request
import uuid
from typing import Any, Optional, Tuple

from fastapi import APIRouter, Request

from routes.provider_mock import MockWebhookIn, provider_mock_webhook

router = APIRouter()


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value:
        return value.strip()

    # Fallback for environments where .env is not loaded into os.environ.
    try:
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    except Exception:
        pass

    return default


def _get_any(data: Any, *paths: Tuple[str, ...]) -> Any:
    for path in paths:
        cur = data
        ok = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur.get(key)
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _normalize_phone_or_jid(value: Optional[str]) -> str:
    if not value:
        return ""

    text = str(value).strip()
    for suffix in ("@c.us", "@s.whatsapp.net"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _extract_message_id(payload: dict, msg: dict) -> str:
    value = _get_any(
        msg,
        ("id",),
        ("id", "id"),
        ("_data", "id", "id"),
        ("_data", "id", "_serialized"),
    ) or _get_any(payload, ("id",), ("message_id",))

    if isinstance(value, dict):
        value = value.get("id") or value.get("_serialized") or json.dumps(value, sort_keys=True)

    return str(value or f"waha_{uuid.uuid4().hex[:16]}")


def _extract_text(msg: dict) -> Tuple[str, str]:
    text = _get_any(
        msg,
        ("body",),
        ("text",),
        ("caption",),
        ("message",),
        ("_data", "body"),
        ("_data", "caption"),
    )

    msg_type = str(
        _get_any(msg, ("type",), ("messageType",), ("_data", "type")) or "unknown"
    )

    if isinstance(text, dict):
        text = text.get("body") or text.get("text") or json.dumps(text, ensure_ascii=False)

    if text is None:
        text = ""

    text = str(text).strip()

    if not text:
        if bool(_get_any(msg, ("hasMedia",), ("_data", "hasMedia"))):
            text = f"Pelanggan mengirim media tipe {msg_type}. Mohon dicek owner."
        else:
            text = f"Pelanggan mengirim pesan tipe {msg_type}. Mohon dicek owner."

    return text, msg_type


def _send_waha_text(chat_id: str, text: str) -> dict:
    base_url = _env("WAHA_BASE_URL").rstrip("/")
    api_key = _env("WAHA_API_KEY")
    session = _env("WAHA_SESSION", "default")

    if not base_url or not api_key:
        return {
            "ok": False,
            "skipped": True,
            "reason": "WAHA_BASE_URL or WAHA_API_KEY is not configured",
        }

    body = json.dumps({
        "session": session,
        "chatId": chat_id,
        "text": text,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/api/sendText",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": api_key,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw = res.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {"raw": raw}
            return {
                "ok": 200 <= res.status < 300,
                "status_code": res.status,
                "response": parsed,
            }
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw": raw}
        return {
            "ok": False,
            "status_code": e.code,
            "response": parsed,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }


@router.post("/provider/waha/webhook")
async def provider_waha_webhook(request: Request):
    raw = await request.json()

    # Lightweight reachability probe.
    if raw.get("healthcheck") is True:
        return {"ok": True, "provider": "waha", "healthcheck": True}

    event = str(raw.get("event") or raw.get("type") or "").lower()
    payload = raw.get("payload") or raw.get("data") or raw.get("message") or raw

    if not isinstance(payload, dict):
        return {"ok": True, "provider": "waha", "ignored": True, "reason": "payload_not_dict"}

    # WAHA message object is usually in payload. Keep this robust for variants.
    msg = payload
    if isinstance(payload.get("message"), dict):
        msg = payload.get("message") or payload

    from_me = bool(_get_any(msg, ("fromMe",), ("from_me",), ("_data", "id", "fromMe")))
    if from_me:
        return {"ok": True, "provider": "waha", "ignored": True, "reason": "from_me"}

    chat_id = str(
        _get_any(
            msg,
            ("from",),
            ("chatId",),
            ("chat_id",),
            ("fromId",),
            ("_data", "from"),
            ("_data", "id", "remote"),
        )
        or ""
    ).strip()

    if not chat_id:
        return {"ok": True, "provider": "waha", "ignored": True, "reason": "missing_chat_id"}

    if chat_id in ("status@broadcast", "broadcast") or chat_id.endswith("@g.us"):
        return {
            "ok": True,
            "provider": "waha",
            "ignored": True,
            "reason": "broadcast_or_group_ignored",
            "chat_id": chat_id,
        }

    message_text, message_type = _extract_text(msg)
    provider_message_id = _extract_message_id(raw, msg)

    shop_id = (
        raw.get("shop_id")
        or payload.get("shop_id")
        or _env("WAHA_DEFAULT_SHOP_ID", "spacecraft-main")
    )
    provider_phone = (
        raw.get("provider_phone")
        or payload.get("provider_phone")
        or _env("WAHA_PROVIDER_PHONE", "")
    )

    customer_phone = _normalize_phone_or_jid(chat_id)
    customer_name = (
        _get_any(msg, ("pushName",), ("notifyName",), ("name",), ("_data", "notifyName"))
        or customer_phone
    )

    mock_input = MockWebhookIn(
        shop_id=shop_id,
        provider_phone=provider_phone or None,
        customer_phone=customer_phone,
        customer_name=str(customer_name),
        message=message_text,
        provider_message_id=f"waha_in_{provider_message_id}",
    )

    result = await provider_mock_webhook(mock_input)

    send_result = None
    should_send = (
        isinstance(result, dict)
        and result.get("provider_status") == "sent_mock"
        and bool(result.get("bot_reply"))
    )

    if should_send:
        send_result = _send_waha_text(chat_id, str(result.get("bot_reply")))

    return {
        "ok": True,
        "provider": "waha",
        "event": event,
        "chat_id": chat_id,
        "customer_phone": customer_phone,
        "message_type": message_type,
        "provider_message_id": provider_message_id,
        "internal": result,
        "send_attempted": bool(should_send),
        "send_result": send_result,
    }
