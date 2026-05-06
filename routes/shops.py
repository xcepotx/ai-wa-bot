"""Shop routes: CRUD toko, produk, payment info."""
from fastapi import APIRouter, HTTPException, Request

from deps import db, require_user, new_id, now_iso
from models import ShopIn, ProductIn, PaymentIn, BotProfileIn

router = APIRouter()


# ── Helper ────────────────────────────────────────────────
async def _get_user_shop(user: dict) -> dict:
    shop_id = user.get("shop_id")
    if not shop_id:
        raise HTTPException(status_code=404, detail="Belum punya toko. Buat toko dulu.")
    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Toko tidak ditemukan")
    return shop


async def _require_own_shop(user: dict, shop_id: str) -> dict:
    if user.get("shop_id") != shop_id and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Bukan toko kamu")
    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0})
    if not shop:
        raise HTTPException(status_code=404, detail="Toko tidak ditemukan")
    return shop


# ── Shop CRUD ─────────────────────────────────────────────
@router.post("/shops")
async def create_shop(data: ShopIn, request: Request):
    user = await require_user(request)

    if user.get("shop_id"):
        raise HTTPException(status_code=400, detail="Sudah punya toko. Edit toko yang ada.")

    shop_id = new_id("shop")
    now     = now_iso()

    shop = {
        "shop_id":      shop_id,
        "owner_user_id": user["user_id"],
        "source":       "standalone",      # standalone | lapakin
        "name":         data.name.strip(),
        "description":  data.description or "",
        "business_type": data.business_type or "",
        "whatsapp":     data.whatsapp or "",
        "address":      data.address or "",
        "hours":        data.hours or "",
        "about":        data.about or "",
        "is_active":    data.is_active,
        "created_at":   now,
        "updated_at":   now,
    }
    await db.shops.insert_one(shop)

    # Update user dengan shop_id
    await db.users.update_one(
        {"user_id": user["user_id"]},
        {"$set": {"shop_id": shop_id, "updated_at": now}},
    )

    shop.pop("_id", None)
    return {"ok": True, "shop": shop}


@router.get("/shops/me")
async def get_my_shop(request: Request):
    user = await require_user(request)
    shop = await _get_user_shop(user)

    # Enrich dengan data tambahan
    products = await db.products.find(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(200)

    payment = await db.payment_info.find_one(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ) or {}

    bot_profile = await db.bot_shop_profile.find_one(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ) or {}

    return {
        "shop":        shop,
        "products":    products,
        "payment":     payment,
        "bot_profile": bot_profile,
    }


@router.put("/shops/me")
async def update_my_shop(data: ShopIn, request: Request):
    user = await require_user(request)
    shop = await _get_user_shop(user)
    now  = now_iso()

    update = {
        "name":          data.name.strip(),
        "description":   data.description or "",
        "business_type": data.business_type or "",
        "whatsapp":      data.whatsapp or "",
        "address":       data.address or "",
        "hours":         data.hours or "",
        "about":         data.about or "",
        "is_active":     data.is_active,
        "updated_at":    now,
    }
    await db.shops.update_one({"shop_id": shop["shop_id"]}, {"$set": update})
    return {"ok": True}


# ── Payment Info ──────────────────────────────────────────
@router.get("/shops/me/payment")
async def get_payment(request: Request):
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    payment = await db.payment_info.find_one(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ) or {}
    return {"payment": payment}


@router.put("/shops/me/payment")
async def update_payment(data: PaymentIn, request: Request):
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    shop_id = shop["shop_id"]
    now     = now_iso()

    update = {
        "shop_id":        shop_id,
        "qris_available": data.qris_available,
        "qris_image_url": data.qris_image_url or "",
        "bank_accounts":  [b.model_dump() for b in (data.bank_accounts or [])],
        "cod_available":  data.cod_available,
        "payment_notes":  data.payment_notes or "",
        "instruction":    data.instruction or "",
        "updated_at":     now,
    }
    await db.payment_info.update_one(
        {"shop_id": shop_id},
        {"$set": update, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"ok": True}


# ── Bot Profile (data operasional) ────────────────────────
@router.get("/shops/me/bot-profile")
async def get_bot_profile(request: Request):
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    profile = await db.bot_shop_profile.find_one(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ) or {}
    return {"profile": profile}


@router.put("/shops/me/bot-profile")
async def update_bot_profile(data: BotProfileIn, request: Request):
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    shop_id = shop["shop_id"]
    now     = now_iso()

    update = {
        "shop_id":        shop_id,
        "order_methods":  data.order_methods or [],
        "service_area":   data.service_area or "",
        "min_order":      data.min_order,
        "preorder_policy": data.preorder_policy or "",
        "store_notes":    data.store_notes or "",
        "updated_at":     now,
    }
    await db.bot_shop_profile.update_one(
        {"shop_id": shop_id},
        {"$set": update, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {"ok": True}


# ── Products CRUD ─────────────────────────────────────────
@router.get("/shops/me/products")
async def list_products(request: Request):
    user     = await require_user(request)
    shop     = await _get_user_shop(user)
    products = await db.products.find(
        {"shop_id": shop["shop_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    return {"products": products, "total": len(products)}


@router.post("/shops/me/products")
async def create_product(data: ProductIn, request: Request):
    user    = await require_user(request)
    shop    = await _get_user_shop(user)
    now     = now_iso()

    product_id = new_id("prod")
    doc = {
        "product_id":    product_id,
        "shop_id":       shop["shop_id"],
        "name":          data.name.strip(),
        "price":         data.price,
        "stock":         data.stock or 0,
        "description":   data.description or "",
        "category":      data.category or "",
        "is_active":     data.is_active,
        "is_available":  data.is_available,
        "is_recommended": data.is_recommended,
        "promo_label":   data.promo_label or "",
        "created_at":    now,
        "updated_at":    now,
    }
    await db.products.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, "product": doc}


@router.put("/shops/me/products/{product_id}")
async def update_product(product_id: str, data: ProductIn, request: Request):
    user = await require_user(request)
    shop = await _get_user_shop(user)
    now  = now_iso()

    result = await db.products.update_one(
        {"product_id": product_id, "shop_id": shop["shop_id"]},
        {"$set": {
            "name":          data.name.strip(),
            "price":         data.price,
            "stock":         data.stock or 0,
            "description":   data.description or "",
            "category":      data.category or "",
            "is_active":     data.is_active,
            "is_available":  data.is_available,
            "is_recommended": data.is_recommended,
            "promo_label":   data.promo_label or "",
            "updated_at":    now,
        }},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan")
    return {"ok": True}


@router.delete("/shops/me/products/{product_id}")
async def delete_product(product_id: str, request: Request):
    user   = await require_user(request)
    shop   = await _get_user_shop(user)
    result = await db.products.delete_one(
        {"product_id": product_id, "shop_id": shop["shop_id"]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Produk tidak ditemukan")
    return {"ok": True}
