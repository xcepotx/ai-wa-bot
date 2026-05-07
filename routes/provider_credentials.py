"""Provider credentials management for Lapakin Asisten."""
import secrets
from typing import Optional, Dict, Any

from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from deps import db, require_admin, now_iso, new_id
from provider_security import encrypt_secret, secret_last4

router = APIRouter()

SUPPORTED_PROVIDERS = {"mock", "meta_cloud"}
SUPPORTED_STATUS = {"draft", "configured", "connected", "disabled"}


class ProviderCredentialIn(BaseModel):
    provider: str = "meta_cloud"
    status: str = "configured"
    enabled: bool = False

    display_name: Optional[str] = None
    display_phone_number: Optional[str] = None

    phone_number_id: Optional[str] = None
    waba_id: Optional[str] = None
    business_account_id: Optional[str] = None

    access_token: Optional[str] = None
    verify_token: Optional[str] = None
    webhook_secret: Optional[str] = None

    notes: Optional[str] = None


class ProviderCredentialDisableIn(BaseModel):
    reason: Optional[str] = None


def _clean_provider(value: str) -> str:
    provider = (value or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Provider tidak valid. Gunakan: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
        )
    return provider


def _clean_status(value: str) -> str:
    status = (value or "").strip().lower()
    if status not in SUPPORTED_STATUS:
        raise HTTPException(
            status_code=400,
            detail=f"Status tidak valid. Gunakan: {', '.join(sorted(SUPPORTED_STATUS))}."
        )
    return status


async def _shop_or_404(shop_id: str) -> dict:
    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Shop tidak ditemukan")
    return shop


def _public_credential(doc: Optional[dict], shop: Optional[dict] = None) -> dict:
    if not doc:
        return {
            "configured": False,
            "credential": None,
            "shop": shop or {},
        }

    public = {
        k: v
        for k, v in doc.items()
        if k not in {
            "_id",
            "access_token_encrypted",
            "webhook_secret_encrypted",
        }
    }

    public["configured"] = True
    public["access_token_present"] = bool(doc.get("access_token_encrypted"))
    public["access_token_last4"] = doc.get("access_token_last4") or ""
    public["webhook_secret_present"] = bool(doc.get("webhook_secret_encrypted"))
    public["shop"] = shop or {}

    return public


def _missing_fields(doc: dict) -> list:
    provider = doc.get("provider")

    if provider == "mock":
        return []

    missing = []

    for key in ["phone_number_id", "waba_id"]:
        if not doc.get(key):
            missing.append(key)

    if not doc.get("access_token_encrypted"):
        missing.append("access_token")

    if not doc.get("verify_token"):
        missing.append("verify_token")

    return missing


async def _write_event(shop_id: str, event_type: str, payload: Dict[str, Any]):
    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": event_type,
        "payload": payload or {},
        "created_at": now_iso(),
    })


@router.get("/admin/provider-credentials")
async def admin_provider_credentials(
    request: Request,
    provider: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    shop_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=200),
):
    await require_admin(request)

    query = {}

    if provider:
        query["provider"] = _clean_provider(provider)

    if status:
        query["status"] = _clean_status(status)

    if shop_id:
        query["shop_id"] = shop_id

    rows = await db.provider_credentials.find(query, {"_id": 0}) \
        .sort("updated_at", -1) \
        .limit(limit) \
        .to_list(limit)

    shop_ids = [r.get("shop_id") for r in rows if r.get("shop_id")]
    shops = {}
    if shop_ids:
        shop_rows = await db.shops.find(
            {"shop_id": {"$in": list(set(shop_ids))}},
            {"_id": 0, "shop_id": 1, "name": 1, "source": 1, "whatsapp": 1},
        ).to_list(len(shop_ids))
        shops = {s["shop_id"]: s for s in shop_rows}

    return {
        "items": [_public_credential(r, shops.get(r.get("shop_id"), {})) for r in rows],
        "total": len(rows),
    }


@router.get("/admin/shops/{shop_id}/provider-credentials")
async def admin_get_shop_provider_credentials(shop_id: str, request: Request):
    await require_admin(request)
    shop = await _shop_or_404(shop_id)

    doc = await db.provider_credentials.find_one({"shop_id": shop_id}, {"_id": 0})
    return _public_credential(doc, shop)


@router.put("/admin/shops/{shop_id}/provider-credentials")
async def admin_save_shop_provider_credentials(
    shop_id: str,
    data: ProviderCredentialIn,
    request: Request,
):
    admin = await require_admin(request)
    shop = await _shop_or_404(shop_id)

    provider = _clean_provider(data.provider)
    status = _clean_status(data.status)
    now = now_iso()

    existing = await db.provider_credentials.find_one({"shop_id": shop_id}, {"_id": 0}) or {}

    verify_token = data.verify_token or existing.get("verify_token") or secrets.token_urlsafe(32)

    set_doc = {
        "shop_id": shop_id,
        "provider": provider,
        "status": status,
        "enabled": bool(data.enabled),
        "display_name": data.display_name,
        "display_phone_number": data.display_phone_number,
        "phone_number_id": data.phone_number_id,
        "waba_id": data.waba_id,
        "business_account_id": data.business_account_id,
        "verify_token": verify_token,
        "notes": data.notes,
        "updated_at": now,
        "updated_by": admin.get("email") or admin.get("user_id"),
    }

    if data.access_token:
        set_doc["access_token_encrypted"] = encrypt_secret(data.access_token)
        set_doc["access_token_last4"] = secret_last4(data.access_token)

    if data.webhook_secret:
        set_doc["webhook_secret_encrypted"] = encrypt_secret(data.webhook_secret)

    await db.provider_credentials.update_one(
        {"shop_id": shop_id},
        {
            "$set": set_doc,
            "$setOnInsert": {
                "credential_id": new_id("cred"),
                "created_at": now,
                "created_by": admin.get("email") or admin.get("user_id"),
            },
        },
        upsert=True,
    )

    saved = await db.provider_credentials.find_one({"shop_id": shop_id}, {"_id": 0})

    await _write_event(
        shop_id,
        "provider.credentials_saved",
        {
            "provider": provider,
            "status": status,
            "enabled": bool(data.enabled),
            "shop_name": shop.get("name"),
            "admin_email": admin.get("email"),
            "token_present": bool(saved.get("access_token_encrypted")),
        },
    )

    return _public_credential(saved, shop)


@router.post("/admin/shops/{shop_id}/provider-credentials/test")
async def admin_test_shop_provider_credentials(shop_id: str, request: Request):
    await require_admin(request)
    shop = await _shop_or_404(shop_id)

    doc = await db.provider_credentials.find_one({"shop_id": shop_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Credential belum dikonfigurasi")

    missing = _missing_fields(doc)
    ok = len(missing) == 0 and doc.get("status") != "disabled"

    result = {
        "ok": ok,
        "provider": doc.get("provider"),
        "status": doc.get("status"),
        "enabled": bool(doc.get("enabled")),
        "missing_fields": missing,
        "message": "Provider credential siap dipakai." if ok else "Provider credential belum lengkap.",
        "shop": {
            "shop_id": shop.get("shop_id"),
            "name": shop.get("name"),
            "source": shop.get("source"),
        },
    }

    await _write_event(
        shop_id,
        "provider.credentials_tested",
        {
            "provider": doc.get("provider"),
            "ok": ok,
            "missing_fields": missing,
        },
    )

    return result


@router.post("/admin/shops/{shop_id}/provider-credentials/disable")
async def admin_disable_shop_provider_credentials(
    shop_id: str,
    data: ProviderCredentialDisableIn,
    request: Request,
):
    admin = await require_admin(request)
    shop = await _shop_or_404(shop_id)

    result = await db.provider_credentials.update_one(
        {"shop_id": shop_id},
        {
            "$set": {
                "status": "disabled",
                "enabled": False,
                "disabled_at": now_iso(),
                "disabled_by": admin.get("email") or admin.get("user_id"),
                "disable_reason": data.reason or "",
                "updated_at": now_iso(),
            }
        },
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Credential belum dikonfigurasi")

    await _write_event(
        shop_id,
        "provider.credentials_disabled",
        {
            "reason": data.reason or "",
            "shop_name": shop.get("name"),
            "admin_email": admin.get("email"),
        },
    )

    doc = await db.provider_credentials.find_one({"shop_id": shop_id}, {"_id": 0})
    return _public_credential(doc, shop)


@router.delete("/admin/shops/{shop_id}/provider-credentials")
async def admin_delete_shop_provider_credentials(shop_id: str, request: Request):
    admin = await require_admin(request)
    shop = await _shop_or_404(shop_id)

    result = await db.provider_credentials.delete_one({"shop_id": shop_id})

    await _write_event(
        shop_id,
        "provider.credentials_deleted",
        {
            "deleted": result.deleted_count,
            "shop_name": shop.get("name"),
            "admin_email": admin.get("email"),
        },
    )

    return {"ok": True, "deleted": result.deleted_count, "shop_id": shop_id}
