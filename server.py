"""AI WA Bot — standalone FastAPI service. Port: 8002"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="AI WA Bot", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from routes import ALL_ROUTERS
for r in ALL_ROUTERS:
    app.include_router(r, prefix="/api")


@app.on_event("startup")
async def on_startup():
    from deps import db

    # Users
    await db.users.create_index("email", unique=True)
    await db.users.create_index("user_id", unique=True)
    await db.users.create_index("shop_id", sparse=True)

    # Shops
    await db.shops.create_index("shop_id", unique=True)
    await db.shops.create_index("owner_user_id")
    await db.shops.create_index("source")

    # Products
    await db.products.create_index("product_id", unique=True)
    await db.products.create_index("shop_id")

    # Payment
    await db.payment_info.create_index("shop_id", unique=True)

    # Bot
    await db.bot_settings.create_index("shop_id", unique=True)
    await db.bot_shop_profile.create_index("shop_id", unique=True)
    await db.bot_faqs.create_index("shop_id")
    await db.bot_faqs.create_index("faq_id", unique=True)

    # Conversations
    await db.sessions.create_index("session_id", unique=True, sparse=True)
    await db.sessions.create_index("shop_id")
    await db.messages.create_index("shop_id")
    await db.messages.create_index("session_id")


@app.on_event("shutdown")
async def on_shutdown():
    from deps import _client
    _client.close()


@app.get("/health")
async def health():
    return {"ok": True, "service": "ai-wa-bot", "version": "1.0.0"}
