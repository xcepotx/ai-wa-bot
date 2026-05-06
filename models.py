"""Pydantic models untuk ai-wa-bot standalone."""
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: str = Field(min_length=1)
    business_name: Optional[str] = ""   # nama usaha


class LoginIn(BaseModel):
    email: EmailStr
    password: str


# ── Shop ──────────────────────────────────────────────────
class ShopIn(BaseModel):
    name: str = Field(min_length=1)
    description: Optional[str] = ""
    business_type: Optional[str] = ""   # kuliner | fashion | jasa | dll
    whatsapp: Optional[str] = ""
    address: Optional[str] = ""
    hours: Optional[str] = ""           # "Senin-Sabtu 08:00-21:00"
    about: Optional[str] = ""
    is_active: Optional[bool] = True


# ── Product ───────────────────────────────────────────────
class ProductIn(BaseModel):
    name: str = Field(min_length=1)
    price: int = Field(ge=0)
    stock: Optional[int] = Field(ge=0, default=0)
    description: Optional[str] = ""
    category: Optional[str] = ""
    is_active: Optional[bool] = True
    is_available: Optional[bool] = True
    is_recommended: Optional[bool] = False
    promo_label: Optional[str] = ""


# ── Payment ───────────────────────────────────────────────
class BankAccountIn(BaseModel):
    bank: str
    number: str
    name: str


class PaymentIn(BaseModel):
    qris_available: Optional[bool] = False
    qris_image_url: Optional[str] = ""
    bank_accounts: Optional[List[BankAccountIn]] = []
    cod_available: Optional[bool] = False
    payment_notes: Optional[str] = ""
    instruction: Optional[str] = ""


# ── Bot Profile (data operasional toko) ───────────────────
class BotProfileIn(BaseModel):
    order_methods: Optional[List[str]] = []     # pickup|delivery|cod
    service_area: Optional[str] = ""
    min_order: Optional[int] = None
    preorder_policy: Optional[str] = ""
    store_notes: Optional[str] = ""


# ── Bot Settings ──────────────────────────────────────────
class BotSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    # off | simulator_only | draft_only | auto_reply
    tone: Optional[str] = None
    # ramah | santai | profesional | singkat | ceria
    bot_name: Optional[str] = None
    language: Optional[str] = "id"
    outside_hours_message: Optional[str] = None
    fallback_message: Optional[str] = None
    handoff_keywords: Optional[List[str]] = None
    max_auto_replies: Optional[int] = None


# ── FAQ ───────────────────────────────────────────────────
class FAQIn(BaseModel):
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    category: Optional[str] = "lainnya"
    is_active: Optional[bool] = True


# ── Simulate ──────────────────────────────────────────────
class SimulateIn(BaseModel):
    shop_id: str
    customer_message: str
    customer_name: Optional[str] = "Pelanggan"
    session_id: Optional[str] = None
