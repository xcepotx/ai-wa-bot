"""Shared dependencies: db, auth, helpers."""
import os
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
import jwt as pyjwt
from fastapi import HTTPException, Request
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# ── DB ────────────────────────────────────────────────────
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME   = os.environ.get("BOT_DB_NAME", "ai_wa_bot")
_client   = AsyncIOMotorClient(MONGO_URL)
db        = _client[DB_NAME]

# ── JWT ───────────────────────────────────────────────────
JWT_SECRET    = os.environ.get("JWT_SECRET", "changeme-secret-key")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 7

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ai-wa-bot")


# ── Password ──────────────────────────────────────────────
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def create_access_token(user_id: str, email: str) -> str:
    payload = {
        "sub":   user_id,
        "email": email,
        "type":  "access",
        "exp":   datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ── Auth helpers ──────────────────────────────────────────
async def get_user_from_token(request: Request) -> Optional[dict]:
    token = request.cookies.get("access_token") or ""

    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token:
        return None

    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        user = await db.users.find_one(
            {"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0}
        )
        return user
    except Exception:
        return None


async def require_user(request: Request) -> dict:
    user = await get_user_from_token(request)
    if not user:
        raise HTTPException(status_code=401, detail="Tidak terautentikasi")
    return user


async def require_admin(request: Request) -> dict:
    user = await require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


# ── ID generators ─────────────────────────────────────────
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
