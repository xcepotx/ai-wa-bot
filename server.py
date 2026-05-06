"""
AI WA Bot — FastAPI service terpisah dari Lapakin.
Port: 8002
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="AI WA Bot Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME   = os.environ.get("BOT_DB_NAME", "ai_wa_bot")
client    = AsyncIOMotorClient(MONGO_URL)
db        = client[DB_NAME]

from routes import ALL_ROUTERS
for r in ALL_ROUTERS:
    app.include_router(r, prefix="/api")


@app.on_event("startup")
async def on_startup():
    await db.sessions.create_index("session_id", unique=True)
    await db.sessions.create_index("shop_id")
    await db.sessions.create_index("customer_phone")
    await db.messages.create_index("message_id", unique=True)
    await db.messages.create_index([("session_id", 1), ("created_at", 1)])
    await db.messages.create_index("shop_id")


@app.on_event("shutdown")
async def on_shutdown():
    client.close()


@app.get("/health")
async def health():
    return {"ok": True, "service": "ai-wa-bot"}
