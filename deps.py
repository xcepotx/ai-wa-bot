"""Minimal deps untuk llm_service compatibility dengan Lapakin."""
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
DB_NAME   = os.environ.get("BOT_DB_NAME", "ai_wa_bot")

_client = AsyncIOMotorClient(MONGO_URL)
db      = _client[DB_NAME]
