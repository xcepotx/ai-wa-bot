"""Meta Cloud API webhook verification foundation.

This file only handles webhook verification + placeholder POST logging.
Inbound message parsing and outbound sending will be implemented in the next step.
"""
import os
from typing import Optional, Tuple

from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from deps import db, now_iso, new_id, require_admin

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
    """Return whether token is accepted.

    Matching order:
    1. META_WEBHOOK_VERIFY_TOKEN env var for global callback validation.
    2. provider_credentials.verify_token per shop for multi-tenant admin setup.
    """
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


@router.get("/provider/meta/webhook")
async def meta_webhook_verify(request: Request):
    """Meta webhook verification endpoint.

    Meta calls this endpoint with:
    - hub.mode=subscribe
    - hub.verify_token=<token configured in dashboard>
    - hub.challenge=<random challenge>

    If token is valid, return hub.challenge as plain text.
    """
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
    """Placeholder receiver.

    For now we only acknowledge and log the raw event shape.
    Real inbound parsing will be implemented next.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    object_type = body.get("object")
    entries_count = len(body.get("entry") or []) if isinstance(body.get("entry"), list) else 0

    await _write_event(
        None,
        "provider.meta.webhook_received_placeholder",
        {
            "object": object_type,
            "entries_count": entries_count,
            "raw_preview": body,
        },
    )

    # Meta expects a quick 200 OK for webhook delivery.
    return {"ok": True, "received": True, "parser": "placeholder"}


@router.post("/admin/provider/meta/webhook-test")
async def admin_meta_webhook_test(data: MetaWebhookTestIn, request: Request):
    """Admin helper to test token matching without calling Meta."""
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
