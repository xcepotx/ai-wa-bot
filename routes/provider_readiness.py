"""Provider readiness checklist for WhatsApp/real channel activation."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request

from deps import db, require_user, require_admin
from context_service import get_shop_context
from catalog_service import get_effective_products, get_catalog_source_info
from shop_status_service import get_effective_shop_status

try:
    from reply_rules import (
        _extract_products,
        _extract_payment_text,
        _extract_hours_text,
        _to_number,
    )
except Exception:
    _extract_products = None
    _extract_payment_text = None
    _extract_hours_text = None
    _to_number = None


router = APIRouter()


def _get_path(data: Dict[str, Any], path: List[str]) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _truthy_text(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(value)


def _valid_wa(value: Any) -> bool:
    if not value:
        return False
    text = str(value).strip().replace(" ", "").replace("-", "")
    return text.startswith("+62") or text.startswith("62") or text.startswith("08")


def _extract_shop_whatsapp(context: dict, shop_doc: dict) -> str:
    candidates = [
        shop_doc.get("whatsapp"),
        shop_doc.get("whatsapp_number"),
        shop_doc.get("phone"),
        _get_path(context, ["shop", "whatsapp"]),
        _get_path(context, ["shop", "whatsapp_number"]),
        _get_path(context, ["shop", "phone"]),
        context.get("whatsapp"),
        context.get("whatsapp_number"),
    ]
    for value in candidates:
        if value:
            return str(value)
    return ""


def _extract_products_safe(context: dict) -> list:
    if _extract_products:
        try:
            return _extract_products(context)
        except Exception:
            pass

    products = context.get("products") or []
    return products if isinstance(products, list) else []


def _product_price(product: dict) -> Optional[float]:
    if _to_number:
        try:
            return _to_number(product.get("price"))
        except Exception:
            return None

    try:
        return float(product.get("price") or 0)
    except Exception:
        return None




def _product_has_price_info(product: dict) -> bool:
    """Accept numeric price or explicit quote/contact price label as usable price info."""
    for key in ("price", "base_price", "min_price"):
        try:
            raw = product.get(key)
            if raw is not None and float(raw) > 0:
                return True
        except (TypeError, ValueError):
            pass

    label = str(product.get("price_label") or "").strip().lower()
    if label and label not in {"rp 0", "rp0", "0", "none", "null", "-"}:
        return True

    mode = str(product.get("price_mode") or "").strip().lower()
    if mode in {"quote", "contact", "custom", "request_quote"}:
        return True

    return False

def _is_product_active(product: dict) -> bool:
    if product.get("is_active") is False:
        return False

    status = str(product.get("status") or "").lower()
    if status in {"inactive", "deleted", "draft", "hidden", "archived"}:
        return False

    return True


def _extract_payment_safe(context: dict, payment_doc: dict) -> str:
    if payment_doc:
        for key in ["instruction", "payment_instruction", "description", "bank_account", "qris_note"]:
            if payment_doc.get(key):
                return str(payment_doc.get(key))

    if _extract_payment_text:
        try:
            return _extract_payment_text(context)
        except Exception:
            pass

    for path in [
        ["payment_instruction"],
        ["settings", "payment_instruction"],
        ["storefront_settings", "payment_instruction"],
        ["shop", "payment_instruction"],
    ]:
        value = _get_path(context, path)
        if value:
            return str(value)

    return ""


def _extract_hours_safe(context: dict, profile_doc: dict, bot_settings: dict) -> Any:
    for value in [
        profile_doc.get("business_hours") if profile_doc else None,
        bot_settings.get("business_hours") if bot_settings else None,
    ]:
        if _truthy_text(value):
            return value

    if _extract_hours_text:
        try:
            return _extract_hours_text(context)
        except Exception:
            pass

    for path in [
        ["business_hours"],
        ["hours"],
        ["settings", "business_hours"],
        ["storefront_settings", "business_hours"],
        ["shop", "business_hours"],
    ]:
        value = _get_path(context, path)
        if _truthy_text(value):
            return value

    return None


def _add_check(
    checks: list,
    key: str,
    label: str,
    passed: bool,
    points: int,
    detail: str = "",
    blocking: bool = False,
    action_url: str = "",
):
    checks.append({
        "key": key,
        "label": label,
        "passed": bool(passed),
        "points": points,
        "earned": points if passed else 0,
        "detail": detail,
        "blocking": bool(blocking),
        "action_url": action_url,
    })


async def calculate_provider_readiness(shop_id: str) -> dict:
    context = await get_shop_context(shop_id) or {}

    shop_doc = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    if not shop_doc and not context:
        raise HTTPException(status_code=404, detail="Shop tidak ditemukan")

    db_settings = await db.bot_settings.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    context_settings = context.get("bot_settings") if isinstance(context.get("bot_settings"), dict) else {}
    bot_settings = {**context_settings, **db_settings}

    payment_doc = await db.payment_info.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    profile_doc = await db.bot_shop_profile.find_one({"shop_id": shop_id}, {"_id": 0}) or {}

    faqs = context.get("faqs") if isinstance(context.get("faqs"), list) else []
    if not faqs:
        faqs = await db.bot_faqs.find(
            {"shop_id": shop_id, "enabled": {"$ne": False}},
            {"_id": 0},
        ).to_list(100)

    catalog_source_info = await get_catalog_source_info(shop_id, context=context)
    products = await get_effective_products(shop_id, context=context)

    active_products = [p for p in products if isinstance(p, dict) and _is_product_active(p)]
    products_with_price = [p for p in active_products if _product_price(p)]

    control = await db.system_settings.find_one(
        {"key": "lapakin_asisten_control"},
        {"_id": 0},
    ) or {"status": "on"}

    system_status = control.get("status", "on")
    whatsapp = _extract_shop_whatsapp(context, shop_doc)
    payment_text = _extract_payment_safe(context, payment_doc)
    shop_status = await get_effective_shop_status(
        shop_id,
        context=context,
        bot_settings=bot_settings,
        profile_doc=profile_doc,
    )
    hours = shop_status.get("business_hours")

    fallback_message = bot_settings.get("fallback_message")
    mode = bot_settings.get("mode") or "off"
    enabled = bool(bot_settings.get("enabled"))
    admin_disabled = bool(bot_settings.get("admin_disabled"))
    last_simulated_at = bot_settings.get("last_simulated_at")

    checks = []

    _add_check(
        checks,
        "system_on",
        "Global Lapakin Asisten aktif",
        system_status == "on",
        10,
        f"Status saat ini: {system_status}",
        blocking=system_status != "on",
    )

    _add_check(
        checks,
        "not_admin_disabled",
        "Toko tidak dinonaktifkan admin",
        not admin_disabled,
        10,
        bot_settings.get("admin_disable_reason") or "",
        blocking=admin_disabled,
    )

    _add_check(
        checks,
        "whatsapp_available",
        "Nomor WhatsApp toko tersedia",
        _valid_wa(whatsapp),
        10,
        whatsapp or "Nomor WhatsApp belum diisi",
        blocking=not _valid_wa(whatsapp),
        action_url="/dashboard/shop",
    )

    _add_check(
        checks,
        "assistant_enabled",
        "Lapakin Asisten aktif untuk toko",
        enabled and mode != "off",
        10,
        f"enabled={enabled}, mode={mode}",
        blocking=not enabled or mode == "off",
        action_url="/dashboard/bot",
    )

    _add_check(
        checks,
        "products_minimum",
        "Minimal 3 produk aktif",
        len(active_products) >= 3,
        15,
        f"{len(active_products)} produk aktif dari {catalog_source_info.get('catalog_source_label')}",
        blocking=len(active_products) == 0,
        action_url="/dashboard/products",
    )

    _add_check(
        checks,
        "product_prices",
        "Produk aktif punya info harga",
        len(active_products) > 0 and len(products_with_price) == len(active_products),
        5,
        f"{len(products_with_price)}/{len(active_products)} produk punya info harga/estimasi · source: {catalog_source_info.get('catalog_source_label')}",
        action_url="/dashboard/products",
    )

    _add_check(
        checks,
        "payment_instruction",
        "Instruksi pembayaran tersedia",
        _truthy_text(payment_text),
        10,
        "Sudah diisi" if _truthy_text(payment_text) else "Belum ada instruksi pembayaran",
        action_url="/dashboard/shop",
    )

    _add_check(
        checks,
        "faqs_minimum",
        "Minimal 3 FAQ / knowledge",
        len(faqs) >= 3,
        10,
        f"{len(faqs)} FAQ aktif",
        action_url="/dashboard/faqs",
    )

    _add_check(
        checks,
        "business_hours",
        "Jam operasional tersedia",
        _truthy_text(hours),
        5,
        (
            f"Sudah diisi dari {shop_status.get('source_label')} · "
            f"status: {shop_status.get('status_label')} · "
            f"{shop_status.get('business_hours')}"
        ) if _truthy_text(hours) else "Jam operasional belum diisi",
        action_url="/dashboard/shop",
    )

    _add_check(
        checks,
        "fallback_message",
        "Fallback message tersedia",
        _truthy_text(fallback_message),
        10,
        "Sudah diisi" if _truthy_text(fallback_message) else "Fallback belum diatur",
        action_url="/dashboard/bot",
    )

    _add_check(
        checks,
        "simulator_tested",
        "Sudah mencoba simulator",
        _truthy_text(last_simulated_at),
        5,
        last_simulated_at or "Belum ada simulasi",
        action_url="/dashboard/simulator",
    )

    score = sum(c["earned"] for c in checks)
    max_score = sum(c["points"] for c in checks)
    blockers = [c for c in checks if c["blocking"] and not c["passed"]]
    missing = [c for c in checks if not c["passed"]]

    provider_ready = score >= 80 and not blockers

    if blockers:
        status = "blocked"
    elif provider_ready:
        status = "ready"
    else:
        status = "needs_setup"

    return {
        "shop_id": shop_id,
        "shop_name": shop_doc.get("name") or _get_path(context, ["shop", "name"]) or context.get("shop_name"),
        "score": score,
        "max_score": max_score,
        "percentage": round((score / max_score) * 100) if max_score else 0,
        "status": status,
        "provider_ready": provider_ready,
        "minimum_score": 80,
        "system_status": system_status,
        "mode": mode,
        "enabled": enabled,
        "admin_disabled": admin_disabled,
        "shop_status": shop_status,
        "checks": checks,
        "blockers": blockers,
        "missing": missing,
    }


@router.get("/provider-readiness")
async def owner_provider_readiness(request: Request):
    user = await require_user(request)
    shop_id = user.get("shop_id")

    if not shop_id:
        raise HTTPException(status_code=404, detail="Belum punya toko")

    return await calculate_provider_readiness(shop_id)


@router.get("/admin/shops/{shop_id}/provider-readiness")
async def admin_provider_readiness(shop_id: str, request: Request):
    await require_admin(request)
    return await calculate_provider_readiness(shop_id)
