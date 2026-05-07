"""FAQ routes: CRUD knowledge base per toko."""
from fastapi import APIRouter, HTTPException, Request

from deps import db, require_user, new_id, now_iso
from models import FAQIn

router = APIRouter()


async def _get_user_shop(user: dict) -> dict:
    shop_id = user.get("shop_id")
    if not shop_id:
        raise HTTPException(status_code=404, detail="Belum punya toko")
    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Toko tidak ditemukan")
    return shop


@router.get("/faqs")
async def list_faqs(request: Request):
    user  = await require_user(request)
    shop  = await _get_user_shop(user)
    faqs  = await db.bot_faqs.find(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ).sort("category", 1).to_list(500)

    # Normalize older/AI-generated FAQ docs so UI can always render them.
    for faq in faqs:
        if not faq.get("category"):
            faq["category"] = "lainnya"

        if faq.get("is_active") is None:
            faq["is_active"] = bool(
                faq.get("enabled", True)
                and faq.get("active", True)
                and faq.get("status", "active") != "inactive"
            )

    return {"faqs": faqs, "total": len(faqs)}


@router.post("/faqs")
async def create_faq(data: FAQIn, request: Request):
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    now     = now_iso()
    faq_id  = new_id("faq")

    doc = {
        "faq_id":    faq_id,
        "shop_id":   shop["shop_id"],
        "question":  data.question.strip(),
        "answer":    data.answer.strip(),
        "category":  data.category or "lainnya",
        "is_active": data.is_active,
        "source":    "manual",
        "hit_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.bot_faqs.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, "faq": doc}


@router.put("/faqs/{faq_id}")
async def update_faq(faq_id: str, data: FAQIn, request: Request):
    user = await require_user(request)
    shop = await _get_user_shop(user)
    now  = now_iso()

    result = await db.bot_faqs.update_one(
        {"faq_id": faq_id, "shop_id": shop["shop_id"]},
        {"$set": {
            "question":  data.question.strip(),
            "answer":    data.answer.strip(),
            "category":  data.category or "lainnya",
            "is_active": data.is_active,
            "updated_at": now,
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="FAQ tidak ditemukan")
    return {"ok": True}


@router.delete("/faqs/{faq_id}")
async def delete_faq(faq_id: str, request: Request):
    user   = await require_user(request)
    shop   = await _get_user_shop(user)
    result = await db.bot_faqs.delete_one(
        {"faq_id": faq_id, "shop_id": shop["shop_id"]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="FAQ tidak ditemukan")
    return {"ok": True}


# ── AI Enhanced FAQ Generator ─────────────────────────────

from typing import Optional, List
from fastapi import Request
from pydantic import BaseModel, Field

from deps import db, now_iso, new_id, require_user
from catalog_service import get_effective_products, get_catalog_source_info
from shop_status_service import get_effective_shop_status


class AIFAQSuggestIn(BaseModel):
    shop_id: Optional[str] = None
    count: int = Field(default=8, ge=3, le=15)


class AIFAQItemIn(BaseModel):
    question: str
    answer: str
    enabled: bool = True
    source: Optional[str] = "ai_suggested"


class AIFAQBulkCreateIn(BaseModel):
    shop_id: Optional[str] = None
    items: List[AIFAQItemIn]


async def _faq_current_user_shop(request: Request, requested_shop_id: Optional[str] = None) -> tuple[dict, str]:
    user = await require_user(request)

    if not user:
        raise HTTPException(status_code=401, detail="Tidak terautentikasi")

    role = user.get("role")
    user_shop_id = user.get("shop_id")

    if role == "admin" and requested_shop_id:
        return user, requested_shop_id

    if user_shop_id:
        return user, user_shop_id

    if requested_shop_id:
        return user, requested_shop_id

    raise HTTPException(status_code=400, detail="shop_id tidak ditemukan untuk user ini")


def _format_price(value) -> str:
    try:
        num = float(str(value).replace(".", "").replace(",", "."))
        if num <= 0:
            return ""
        return "Rp{:,.0f}".format(num).replace(",", ".")
    except Exception:
        return ""


def _product_name(p: dict) -> str:
    return str(
        p.get("name")
        or p.get("title")
        or p.get("product_name")
        or p.get("nama")
        or ""
    ).strip()


def _product_price(p: dict) -> str:
    return _format_price(
        p.get("price")
        or p.get("selling_price")
        or p.get("sale_price")
        or p.get("harga")
        or p.get("amount")
    )


def _clean_text(value) -> str:
    return str(value or "").strip()


def _unique_faqs(items: list[dict], limit: int) -> list[dict]:
    seen = set()
    out = []

    for item in items:
        q = _clean_text(item.get("question"))
        a = _clean_text(item.get("answer"))

        if not q or not a:
            continue

        key = q.lower()
        if key in seen:
            continue

        seen.add(key)
        out.append({
            "question": q,
            "answer": a,
            "source": "ai_suggested",
            "confidence": item.get("confidence") or "high",
            "reason": item.get("reason") or "",
        })

        if len(out) >= limit:
            break

    return out


async def _build_ai_faq_suggestions(shop_id: str, count: int = 8) -> dict:
    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    settings = await db.bot_settings.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    profile = await db.bot_shop_profile.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    payment = await db.payment_info.find_one({"shop_id": shop_id}, {"_id": 0}) or {}

    products = await get_effective_products(shop_id)
    catalog_info = await get_catalog_source_info(shop_id)
    shop_status = await get_effective_shop_status(
        shop_id,
        bot_settings=settings,
        profile_doc=profile,
    )

    shop_name = (
        shop.get("name")
        or shop_status.get("shop_name")
        or "toko kami"
    )

    business_hours = shop_status.get("business_hours")
    status_label = shop_status.get("status_label")
    address = profile.get("address") or shop.get("address") or shop.get("location")
    payment_text = (
        payment.get("instruction")
        or payment.get("payment_instruction")
        or payment.get("description")
        or profile.get("payment_instruction")
    )

    active_products = [p for p in products if _product_name(p)]
    product_names = [_product_name(p) for p in active_products[:8]]
    priced_products = [
        {
            "name": _product_name(p),
            "price": _product_price(p),
        }
        for p in active_products
        if _product_price(p)
    ]

    items = []

    if product_names:
        if len(product_names) <= 5:
            product_list = ", ".join(product_names)
        else:
            product_list = ", ".join(product_names[:5]) + ", dan menu lainnya"

        items.append({
            "question": "Apa saja produk atau menu yang tersedia?",
            "answer": f"{shop_name} menyediakan beberapa produk/menu seperti {product_list}. Untuk daftar terbaru, silakan cek katalog atau tanyakan produk yang Kakak cari.",
            "reason": "Generated from effective product catalog",
        })

    for product in priced_products[:3]:
        items.append({
            "question": f"Berapa harga {product['name']}?",
            "answer": f"Harga {product['name']} adalah {product['price']}. Harga dapat berubah sewaktu-waktu mengikuti update dari toko.",
            "reason": "Generated from priced products",
        })

    if payment_text:
        items.append({
            "question": "Metode pembayaran apa saja yang tersedia?",
            "answer": f"Untuk pembayaran, {payment_text}",
            "reason": "Generated from payment instruction",
        })
    else:
        items.append({
            "question": "Apakah bisa bayar transfer atau QRIS?",
            "answer": "Untuk metode pembayaran, Kakak bisa konfirmasi ke owner agar mendapatkan instruksi pembayaran yang paling sesuai.",
            "confidence": "medium",
            "reason": "Payment data missing, safe handoff answer",
        })

    if business_hours:
        items.append({
            "question": "Jam buka toko jam berapa?",
            "answer": f"Jam operasional {shop_name}: {business_hours}. Status saat ini: {status_label or 'belum diketahui'}.",
            "reason": "Generated from effective business hours",
        })
    else:
        items.append({
            "question": "Toko buka jam berapa?",
            "answer": "Untuk jam buka toko, Kakak bisa cek info terbaru di toko atau menunggu konfirmasi dari owner.",
            "confidence": "medium",
            "reason": "Business hours missing, safe handoff answer",
        })

    if address:
        items.append({
            "question": "Alamat toko di mana?",
            "answer": f"Alamat {shop_name}: {address}.",
            "reason": "Generated from shop profile address",
        })

    items.append({
        "question": "Apakah bisa pesan banyak atau grosir?",
        "answer": "Bisa Kak, untuk pesanan dalam jumlah banyak akan dibantu konfirmasi oleh owner agar stok, estimasi waktu, dan total harga bisa dipastikan.",
        "reason": "Safe common FAQ",
    })

    items.append({
        "question": "Bagaimana cara melakukan pemesanan?",
        "answer": "Kakak bisa menyebutkan produk/menu yang ingin dipesan beserta jumlahnya. Nanti pesanan akan dibantu dicatat dan dikonfirmasi oleh owner jika diperlukan.",
        "reason": "Safe ordering FAQ",
    })

    items.append({
        "question": "Apakah stok produk selalu tersedia?",
        "answer": "Ketersediaan stok bisa berubah sewaktu-waktu. Jika Kakak menanyakan stok produk tertentu, kami akan bantu konfirmasi ke owner.",
        "reason": "Stock-safe answer to prevent hallucination",
    })

    fallback_message = settings.get("fallback_message")
    if fallback_message:
        items.append({
            "question": "Bagaimana jika pertanyaan saya belum bisa dijawab?",
            "answer": fallback_message,
            "reason": "Generated from fallback message",
        })

    suggestions = _unique_faqs(items, count)

    return {
        "shop": {
            "shop_id": shop_id,
            "name": shop_name,
            "source": shop.get("source"),
            "lapakin_shop_id": shop.get("lapakin_shop_id"),
        },
        "catalog_source": catalog_info,
        "shop_status": shop_status,
        "items": suggestions,
        "total": len(suggestions),
        "engine": "ai_enhanced_data_aware_v1",
        "note": "FAQ dibuat dari data toko yang tersedia. Owner disarankan review sebelum menyimpan.",
    }


@router.post("/faqs/ai-suggest")
async def ai_suggest_faqs(data: AIFAQSuggestIn, request: Request):
    user, shop_id = await _faq_current_user_shop(request, data.shop_id)

    result = await _build_ai_faq_suggestions(shop_id, data.count)

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "faq.ai_suggested",
        "payload": {
            "count": result.get("total", 0),
            "engine": result.get("engine"),
            "user_email": user.get("email"),
            "catalog_source": result.get("catalog_source", {}).get("catalog_source"),
        },
        "created_at": now_iso(),
    })

    return result


@router.post("/faqs/bulk-create")
async def bulk_create_faqs(data: AIFAQBulkCreateIn, request: Request):
    user, shop_id = await _faq_current_user_shop(request, data.shop_id)

    if not data.items:
        raise HTTPException(status_code=400, detail="Tidak ada FAQ untuk disimpan")

    now = now_iso()

    existing = await db.bot_faqs.find(
        {"shop_id": shop_id},
        {"_id": 0, "question": 1},
    ).to_list(1000)

    existing_questions = {
        _clean_text(x.get("question")).lower()
        for x in existing
        if _clean_text(x.get("question"))
    }

    docs = []
    skipped = []

    for item in data.items:
        q = _clean_text(item.question)
        a = _clean_text(item.answer)

        if not q or not a:
            skipped.append({"question": q, "reason": "question/answer kosong"})
            continue

        if q.lower() in existing_questions:
            skipped.append({"question": q, "reason": "duplikat"})
            continue

        existing_questions.add(q.lower())

        is_enabled = bool(item.enabled)

        docs.append({
            "faq_id": new_id("faq"),
            "shop_id": shop_id,
            "question": q,
            "answer": a,
            "enabled": is_enabled,
            "active": is_enabled,
            "is_active": is_enabled,
            "status": "active" if is_enabled else "inactive",
            "category": "lainnya",
            "source": item.source or "ai_suggested",
            "created_by": user.get("email") or user.get("user_id"),
            "created_at": now,
            "updated_at": now,
        })

    created_items = [dict(doc) for doc in docs]

    if docs:
        await db.bot_faqs.insert_many(docs)

    # insert_many mutates docs by adding Mongo ObjectId `_id`.
    # Never return ObjectId directly because FastAPI cannot JSON-serialize it.
    for item in created_items:
        item.pop("_id", None)

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "faq.bulk_created",
        "payload": {
            "created": len(created_items),
            "skipped": len(skipped),
            "source": "ai_enhanced",
            "user_email": user.get("email"),
        },
        "created_at": now,
    })

    return {
        "ok": True,
        "created": len(created_items),
        "skipped": skipped,
        "items": created_items,
    }
