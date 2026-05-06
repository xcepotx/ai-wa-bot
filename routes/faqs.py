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
