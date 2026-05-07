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
    await db.sessions.create_index([("shop_id", 1), ("status", 1), ("updated_at", -1)])
    await db.sessions.create_index([("shop_id", 1), ("updated_at", -1)])
    await db.sessions.create_index("customer_phone")

    await db.messages.create_index("shop_id")
    await db.messages.create_index("session_id")
    await db.messages.create_index([("session_id", 1), ("created_at", 1)])
    await db.messages.create_index([("shop_id", 1), ("created_at", -1)])

    # Safety control / events
    await db.system_settings.create_index("key", unique=True)
    await db.bot_events.create_index([("created_at", -1)])
    await db.bot_events.create_index([("shop_id", 1), ("created_at", -1)])
    await db.bot_events.create_index("type")
    # Provider mock / provider adapter
    await db.provider_messages.create_index([("provider", 1), ("created_at", -1)])
    # Provider credentials
    await db.provider_credentials.create_index("shop_id", unique=True)
    await db.provider_credentials.create_index([("provider", 1), ("status", 1)])
    await db.provider_credentials.create_index([("enabled", 1), ("updated_at", -1)])

    await db.provider_messages.create_index([("provider", 1), ("shop_id", 1), ("created_at", -1)])
    await db.provider_messages.create_index([("provider", 1), ("direction", 1), ("status", 1)])
    await db.provider_messages.create_index([("provider", 1), ("shop_id", 1), ("provider_message_id", 1)], unique=True, sparse=True)

    await db.bot_events.create_index("event_id", unique=True, sparse=True)
    await db.bot_events.create_index([("type", 1), ("created_at", -1)])

    # Admin monitoring
    await db.sessions.create_index("status")
    await db.sessions.create_index([("status", 1), ("updated_at", -1)])
    await db.sessions.create_index([("shop_id", 1), ("status", 1), ("updated_at", -1)])
    await db.messages.create_index([("created_at", -1)])



@app.on_event("shutdown")
async def on_shutdown():
    from deps import _client
    _client.close()


@app.get("/health")
async def health():
    return {"ok": True, "service": "ai-wa-bot", "version": "1.0.0"}
