"""Auth routes: register, login, me, logout."""
from fastapi import APIRouter, HTTPException, Request, Response

from deps import db, hash_password, verify_password, create_access_token, \
    require_user, new_id, now_iso
from models import RegisterIn, LoginIn

router = APIRouter()


@router.post("/auth/register")
async def register(data: RegisterIn):
    email = data.email.lower().strip()

    existing = await db.users.find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="Email sudah terdaftar")

    user_id = new_id("usr")
    now     = now_iso()

    user = {
        "user_id":       user_id,
        "email":         email,
        "password_hash": hash_password(data.password),
        "name":          data.name.strip(),
        "business_name": (data.business_name or "").strip(),
        "role":          "owner",
        "plan":          "free",           # free | starter | pro | business
        "shop_id":       None,             # set setelah buat toko
        "created_at":    now,
        "updated_at":    now,
    }
    await db.users.insert_one(user)

    token = create_access_token(user_id, email)
    user.pop("_id", None)
    user.pop("password_hash", None)

    return {
        "access_token": token,
        "user": user,
    }


@router.post("/auth/login")
async def login(data: LoginIn, response: Response):
    email = data.email.lower().strip()
    user  = await db.users.find_one({"email": email})

    if not user or not verify_password(data.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Email atau password salah")

    token = create_access_token(user["user_id"], email)

    # Set cookie + return token (support keduanya)
    response.set_cookie(
        key="access_token", value=token,
        httponly=True, samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 hari
    )

    user.pop("_id", None)
    user.pop("password_hash", None)

    return {
        "access_token": token,
        "user": user,
    }


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.get("/auth/me")
async def me(request: Request):
    user = await require_user(request)
    return {"user": user}
