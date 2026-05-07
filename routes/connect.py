"""
AI WA Bot — Lapakin Connect Handler
GET /api/connect?token=xxx

Flow:
1. User klik tombol di Lapakin → redirect ke bot.dev/connect?token=xxx
2. Frontend bot.dev load halaman /connect
3. Frontend hit GET /api/connect?token=xxx
4. Backend validate token ke Lapakin API
5. Auto register/login user berdasarkan data Lapakin
6. Return access_token → frontend simpan, redirect ke /dashboard
"""
import os
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from deps import db, hash_password, create_access_token, new_id, now_iso

logger = logging.getLogger("ai-wa-bot")
router = APIRouter()


@router.get("/connect")
async def connect_from_lapakin(token: str = Query(...)):
    """
    Validate Lapakin connect token dan auto login/register user.
    Dipanggil oleh frontend saat user redirect dari Lapakin.
    """
    lapakin_url = os.environ.get("LAPAKIN_API_URL", "https://dev.lapakin.my.id")
    bot_token   = os.environ.get("BOT_SERVICE_TOKEN", "")

    # 1. Validate token ke Lapakin
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{lapakin_url}/api/bot/connect-token/validate/{token}",
                headers={"X-Bot-Token": bot_token},
            )
        if r.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Token tidak valid: {r.json().get('detail', 'Unknown error')}"
            )
        data    = r.json()
        payload = data["payload"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Lapakin token validate error: %s", e)
        raise HTTPException(status_code=500, detail="Gagal validasi token ke Lapakin")

    # 2. Extract data dari payload
    lapakin_user_id = payload["user_id"]
    email           = payload["email"]
    name            = payload["name"]
    lapakin_shop_id = payload["shop_id"]
    shop_name       = payload.get("shop_name", "")
    whatsapp        = payload.get("whatsapp", "")

    # 3. Cari user existing berdasarkan lapakin_user_id atau email
    user = await db.users.find_one({
        "$or": [
            {"lapakin_user_id": lapakin_user_id},
            {"email": email},
        ]
    }, {"_id": 0, "password_hash": 0})

    if not user:
        # 4a. Register user baru
        user_id = new_id("usr")
        nw      = now_iso()

        # Buat shop entry untuk user Lapakin
        shop_id = new_id("shop")
        shop_doc = {
            "shop_id":        shop_id,
            "owner_user_id":  user_id,
            "source":         "lapakin",           # source = lapakin
            "lapakin_shop_id": lapakin_shop_id,    # referensi ke Lapakin
            "name":           shop_name,
            "whatsapp":       whatsapp,
            "is_active":      True,
            "created_at":     nw,
            "updated_at":     nw,
        }
        await db.shops.insert_one(shop_doc)

        user_doc = {
            "user_id":          user_id,
            "email":            email,
            "password_hash":    hash_password(new_id("pw")),  # random password
            "name":             name,
            "business_name":    shop_name,
            "role":             "owner",
            "plan":             "free",
            "shop_id":          shop_id,
            "source":           "lapakin",
            "lapakin_user_id":  lapakin_user_id,
            "lapakin_shop_id":  lapakin_shop_id,
            "created_at":       nw,
            "updated_at":       nw,
        }
        await db.users.insert_one(user_doc)
        user_doc.pop("_id", None)
        user_doc.pop("password_hash", None)
        user = user_doc

    else:
        # 4b. Update data existing jika ada perubahan
        nw = now_iso()
        update = {
            "lapakin_user_id": lapakin_user_id,
            "lapakin_shop_id": lapakin_shop_id,
            "updated_at":      nw,
        }
        if not user.get("shop_id"):
            # Buat shop jika belum ada
            shop_id  = new_id("shop")
            shop_doc = {
                "shop_id":         shop_id,
                "owner_user_id":   user["user_id"],
                "source":          "lapakin",
                "lapakin_shop_id": lapakin_shop_id,
                "name":            shop_name,
                "whatsapp":        whatsapp,
                "is_active":       True,
                "created_at":      nw,
                "updated_at":      nw,
            }
            await db.shops.insert_one(shop_doc)
            update["shop_id"] = shop_id
        else:
            # Update shop source ke lapakin
            await db.shops.update_one(
                {"shop_id": user["shop_id"]},
                {"$set": {
                    "source":          "lapakin",
                    "lapakin_shop_id": lapakin_shop_id,
                    "updated_at":      nw,
                }}
            )

        await db.users.update_one(
            {"user_id": user["user_id"]},
            {"$set": update}
        )
        user = {**user, **update}

    # 5. Generate access token
    access_token = create_access_token(user["user_id"], email)

    return {
        "ok":           True,
        "access_token": access_token,
        "user":         user,
        "is_new":       not bool(user.get("lapakin_user_id")),
        "source":       "lapakin",
    }
