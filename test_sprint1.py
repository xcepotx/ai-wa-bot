#!/usr/bin/env python3
"""
Smoke test Sprint 1 — standalone ai-wa-bot backend.
Jalankan:
  BOT_URL=http://127.0.0.1:8002 python3 test_sprint1.py
"""
import os, sys, json, time
import requests

BOT_URL  = os.environ.get("BOT_URL", "http://127.0.0.1:8002")
TEST_EMAIL = f"test_{int(time.time())}@test.com"
TEST_PASS  = "test123456"

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []

def req(method, path, body=None, token=None):
    url  = f"{BOT_URL}{path}"
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    try:
        r = requests.request(method, url, json=body, headers=hdrs, timeout=15)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {}
    except Exception as e:
        return 0, {"error": str(e)}

def check(name, condition, detail=""):
    icon = PASS if condition else FAIL
    print(f"  {icon} {name}" + (f" — {detail}" if detail else ""))
    results.append(condition)

print(f"\n{'='*55}")
print(f"  AI WA Bot Sprint 1 Smoke Test")
print(f"  {BOT_URL}")
print(f"{'='*55}\n")

# 1. Health
print("[ 1 ] Health")
s, r = req("GET", "/health")
check("Health OK", s == 200, f"status={s}")

# 2. Register
print("\n[ 2 ] Register")
s, r = req("POST", "/api/auth/register", {
    "email": TEST_EMAIL, "password": TEST_PASS,
    "name": "Test Owner", "business_name": "Toko Test"
})
check("Register OK", s == 200, f"status={s}")
token = r.get("access_token", "")
check("Dapat token", bool(token))
check("User ada", bool(r.get("user")))

# 3. Login
print("\n[ 3 ] Login")
s, r = req("POST", "/api/auth/login", {"email": TEST_EMAIL, "password": TEST_PASS})
check("Login OK", s == 200, f"status={s}")
token = r.get("access_token", token)

# 4. Me
print("\n[ 4 ] Auth Me")
s, r = req("GET", "/api/auth/me", token=token)
check("Me OK", s == 200)
check("Email match", r.get("user", {}).get("email") == TEST_EMAIL)

# 5. Create Shop
print("\n[ 5 ] Create Shop")
s, r = req("POST", "/api/shops", {
    "name": "Warung Test Standalone",
    "description": "Warung makan enak dan murah",
    "business_type": "kuliner",
    "whatsapp": "628123456789",
    "address": "Jl. Test No. 1, Bandung",
    "hours": "Senin-Sabtu 08:00-21:00",
}, token=token)
check("Create shop OK", s == 200, f"status={s}")
shop_id = r.get("shop", {}).get("shop_id", "")
check("Dapat shop_id", bool(shop_id), shop_id)

# 6. Get Shop
print("\n[ 6 ] Get Shop")
s, r = req("GET", "/api/shops/me", token=token)
check("Get shop OK", s == 200)
check("Nama toko benar", r.get("shop", {}).get("name") == "Warung Test Standalone")

# 7. Update Payment
print("\n[ 7 ] Payment Info")
s, r = req("PUT", "/api/shops/me/payment", {
    "qris_available": True,
    "bank_accounts": [{"bank": "BCA", "number": "1234567", "name": "Test Owner"}],
    "payment_notes": "Konfirmasi via WA ya kak",
    "instruction": "Transfer ke BCA atau QRIS",
}, token=token)
check("Update payment OK", s == 200)

# 8. Create Products
print("\n[ 8 ] Products CRUD")
product_ids = []
for p in [
    {"name": "Nasi Goreng", "price": 15000, "description": "Nasi goreng spesial"},
    {"name": "Es Teh", "price": 5000},
    {"name": "Ayam Bakar", "price": 25000, "is_recommended": True},
]:
    s, r = req("POST", "/api/shops/me/products", p, token=token)
    check(f"Create '{p['name']}' OK", s == 200, f"status={s}")
    if r.get("product", {}).get("product_id"):
        product_ids.append(r["product"]["product_id"])

s, r = req("GET", "/api/shops/me/products", token=token)
check("List products OK", s == 200)
check("Ada 3 produk", r.get("total", 0) >= 3, str(r.get("total")))

# 9. FAQ CRUD
print("\n[ 9 ] FAQ CRUD")
faq_ids = []
faqs_data = [
    {"question": "Apakah bisa delivery?", "answer": "Iya bisa, area Bandung", "category": "delivery"},
    {"question": "Berapa minimum order?", "answer": "Minimum order Rp 20.000", "category": "harga"},
    {"question": "Jam buka?", "answer": "Buka Senin-Sabtu 08.00-21.00", "category": "jam_buka"},
    {"question": "Bisa QRIS?", "answer": "Bisa, QRIS tersedia", "category": "payment"},
    {"question": "Ada parkir?", "answer": "Ada parkir gratis di depan", "category": "lokasi"},
]
for f in faqs_data:
    s, r = req("POST", "/api/faqs", f, token=token)
    check(f"FAQ '{f['question'][:20]}...' OK", s == 200)
    if r.get("faq", {}).get("faq_id"):
        faq_ids.append(r["faq"]["faq_id"])

s, r = req("GET", "/api/faqs", token=token)
check("List FAQs OK", s == 200)
check("Ada 5 FAQ", r.get("total", 0) >= 5, str(r.get("total")))

# 10. Bot Settings
print("\n[ 10 ] Bot Settings")
s, r = req("PUT", "/api/bot-settings", {
    "tone": "ramah",
    "bot_name": "Sari",
    "fallback_message": "Maaf kak, silakan hubungi admin ya 🙏",
    "handoff_keywords": ["komplain", "refund", "batal"],
    "mode": "simulator_only",
    "enabled": True,
}, token=token)
check("Update bot settings OK", s == 200)

s, r = req("GET", "/api/bot-settings", token=token)
check("Get bot settings OK", s == 200)
check("Bot name tersimpan", r.get("settings", {}).get("bot_name") == "Sari")

# 11. Readiness Score
print("\n[ 11 ] Readiness Score")
s, r = req("GET", "/api/bot-settings/readiness", token=token)
check("Readiness OK", s == 200)
score = r.get("score", 0)
check("Score > 0", score > 0, f"score={score}")
check("can_simulate True", r.get("can_simulate") == True)
print(f"     Score: {score}/100 — {r.get('label')}")

# 12. Simulate
print("\n[ 12 ] Simulator")
s, r = req("POST", "/api/simulate", {
    "shop_id": shop_id,
    "customer_message": "menu apa aja yang tersedia?",
}, token=token)
check("Simulate OK", s == 200, f"status={s}")
if s == 200:
    check("Ada bot_reply", bool(r.get("bot_reply")))
    check("Source bukan error", r.get("source") != "error")
    print(f"     Reply: {r.get('bot_reply', '')[:80]}...")
    print(f"     Source: {r.get('source')} | {r.get('response_ms')}ms")

# Summary
print(f"\n{'='*55}")
total  = len(results)
passed = sum(results)
failed = total - passed
print(f"  Hasil: {passed}/{total} passed", end="")
if failed:
    print(f"  ({failed} FAILED) ❌")
else:
    print("  🎉 Semua OK!")
print(f"{'='*55}\n")

sys.exit(0 if not failed else 1)
