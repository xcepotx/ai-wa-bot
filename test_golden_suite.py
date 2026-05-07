#!/usr/bin/env python3
import os
import re
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, now_iso, new_id
from safety_policy import evaluate_auto_reply_policy


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
SHOP_ID = os.environ.get("SHOP_ID", "shop_golden_suite")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "golden-owner@test.lapakin.local")


def pretty(title, data):
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(data)


def http(method, path, body=None):
    headers = {"Content-Type": "application/json"}
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE_URL + path, data=payload, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=45) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed
    except Exception as e:
        return 0, {"error": str(e)}


def assert_contains(text, expected, label):
    if expected.lower() not in (text or "").lower():
        raise AssertionError(f"{label}: expected reply to contain {expected!r}, got: {text!r}")


def assert_not_contains_any(text, forbidden, label):
    low = (text or "").lower()
    for item in forbidden:
        if item.lower() in low:
            raise AssertionError(f"{label}: reply must not contain {item!r}, got: {text!r}")


def assert_status(status, data, label):
    if status != 200:
        raise AssertionError(f"{label}: expected HTTP 200, got {status}: {data}")


async def seed_golden_shop():
    now = now_iso()

    await db.system_settings.update_one(
        {"key": "lapakin_asisten_control"},
        {
            "$set": {
                "key": "lapakin_asisten_control",
                "status": "on",
                "reason": "",
                "updated_at": now,
                "updated_by": "golden_suite",
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    await db.users.update_one(
        {"email": OWNER_EMAIL},
        {
            "$set": {
                "user_id": "usr_golden_suite",
                "email": OWNER_EMAIL,
                "name": "Golden Owner",
                "role": "owner",
                "shop_id": SHOP_ID,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
                "password_hash": "not-used",
            },
        },
        upsert=True,
    )

    await db.shops.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "shop_id": SHOP_ID,
                "name": "Warung Golden Test",
                "source": "standalone",
                "owner_user_id": "usr_golden_suite",
                "owner_email": OWNER_EMAIL,
                "whatsapp": "+6281234567000",
                "description": "Warung untuk golden test Lapakin Asisten.",
                "address": "Jakarta Selatan",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    await db.products.delete_many({"shop_id": SHOP_ID})
    await db.products.insert_many([
        {
            "product_id": "prod_golden_nasgor",
            "id": "prod_golden_nasgor",
            "shop_id": SHOP_ID,
            "name": "Nasi Goreng",
            "description": "Nasi goreng spesial.",
            "price": 15000,
            "status": "active",
            "category": "Makanan",
            "created_at": now,
            "updated_at": now,
        },
        {
            "product_id": "prod_golden_esteh",
            "id": "prod_golden_esteh",
            "shop_id": SHOP_ID,
            "name": "Es Teh",
            "description": "Es teh manis.",
            "price": 5000,
            "status": "active",
            "category": "Minuman",
            "created_at": now,
            "updated_at": now,
        },
        {
            "product_id": "prod_golden_ayambakar",
            "id": "prod_golden_ayambakar",
            "shop_id": SHOP_ID,
            "name": "Ayam Bakar",
            "description": "Ayam bakar bumbu kecap.",
            "price": 25000,
            "status": "active",
            "category": "Makanan",
            "created_at": now,
            "updated_at": now,
        },
    ])

    await db.payment_info.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "shop_id": SHOP_ID,
                "instruction": "Bisa bayar via QRIS atau transfer BCA 123456789 a.n. Warung Golden.",
                "payment_instruction": "Bisa bayar via QRIS atau transfer BCA 123456789 a.n. Warung Golden.",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    await db.bot_shop_profile.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "shop_id": SHOP_ID,
                "business_hours": "Senin-Sabtu 08.00-20.00",
                "address": "Jakarta Selatan",
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    await db.bot_faqs.delete_many({"shop_id": SHOP_ID})
    await db.bot_faqs.insert_many([
        {
            "faq_id": new_id("faq"),
            "shop_id": SHOP_ID,
            "question": "Apakah bisa delivery?",
            "answer": "Bisa delivery sekitar area toko dengan konfirmasi ongkir ke owner.",
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "faq_id": new_id("faq"),
            "shop_id": SHOP_ID,
            "question": "Jam buka?",
            "answer": "Kami buka Senin-Sabtu 08.00-20.00.",
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
        {
            "faq_id": new_id("faq"),
            "shop_id": SHOP_ID,
            "question": "Bisa bayar QRIS?",
            "answer": "Bisa bayar via QRIS atau transfer BCA.",
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        },
    ])

    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "shop_id": SHOP_ID,
                "enabled": True,
                "mode": "auto_reply",
                "tone": "ramah",
                "language": "id",
                "fallback_message": "Maaf kak, saya bantu teruskan ke owner ya 🙏",
                "handoff_keywords": ["owner", "admin", "komplain", "refund"],
                "business_hours": "Senin-Sabtu 08.00-20.00",
                "quota_monthly": 100,
                "quota_used": 0,
                "admin_disabled": False,
                "last_simulated_at": now,
                "updated_at": now,
            },
            "$unset": {
                "admin_disable_reason": "",
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


async def reset_safety():
    await db.system_settings.update_one(
        {"key": "lapakin_asisten_control"},
        {"$set": {"status": "on", "reason": "", "updated_at": now_iso()}},
        upsert=True,
    )
    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "enabled": True,
                "mode": "auto_reply",
                "admin_disabled": False,
                "quota_monthly": 100,
                "quota_used": 0,
                "updated_at": now_iso(),
            },
            "$unset": {"admin_disable_reason": ""},
        },
        upsert=True,
    )


async def run_sim(message, session_id=None, customer_name="Golden Tester"):
    payload = {
        "shop_id": SHOP_ID,
        "customer_message": message,
        "customer_name": customer_name,
        "customer_phone": "+6281111111999",
    }
    if session_id:
        payload["session_id"] = session_id

    status, data = http("POST", "/api/simulate", payload)
    assert_status(status, data, f"simulate: {message}")
    return data


async def test_product_list():
    data = await run_sim("Produk apa aja yang tersedia?")
    pretty("GOLDEN: product list", data)

    assert data["source"] == "rule_product_list"
    assert_contains(data["bot_reply"], "Nasi Goreng", "product list")
    assert_contains(data["bot_reply"], "Es Teh", "product list")
    assert_contains(data["bot_reply"], "Ayam Bakar", "product list")


async def test_product_memory_total():
    first = await run_sim("Nasi Goreng ada?")
    pretty("GOLDEN: product memory first", first)

    second = await run_sim("Kalau 2 berapa?", session_id=first["session_id"])
    pretty("GOLDEN: product memory follow-up", second)

    assert second["source"] == "rule_product_memory"
    assert_contains(second["bot_reply"], "Nasi Goreng", "product memory")
    assert_contains(second["bot_reply"], "Rp30.000", "product memory total")


async def test_payment():
    data = await run_sim("Bisa bayar QRIS?")
    pretty("GOLDEN: payment", data)

    assert data["source"] in {"rule_payment", "faq"}
    assert_contains(data["bot_reply"], "QRIS", "payment")


async def test_unknown_product_no_price_hallucination():
    data = await run_sim("Harga Sate Kambing berapa?")
    pretty("GOLDEN: unknown product", data)

    # Unknown product must not fabricate a sate price.
    assert_not_contains_any(
        data["bot_reply"],
        ["Sate Kambing harganya", "Rp15.000", "Rp30.000", "Rp25.000"],
        "unknown product no hallucinated price",
    )


async def test_handoff_keyword():
    data = await run_sim("Saya mau bicara dengan owner")
    pretty("GOLDEN: handoff keyword", data)

    assert data["status"] == "handoff"
    assert data["handoff_required"] is True
    assert data["source"] == "keyword_match"


async def test_global_maintenance_skip():
    await db.system_settings.update_one(
        {"key": "lapakin_asisten_control"},
        {"$set": {"status": "maintenance", "reason": "Golden maintenance test"}},
        upsert=True,
    )

    data = await run_sim("Produk apa aja?")
    pretty("GOLDEN: global maintenance skip", data)

    assert data["status"] == "skipped"
    assert data["source"] == "safety_policy"
    assert data["intent"] == "system_disabled"

    await reset_safety()


async def test_admin_disabled_skip():
    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "admin_disabled": True,
                "admin_disable_reason": "Golden force disable test",
                "updated_at": now_iso(),
            }
        },
        upsert=True,
    )

    data = await run_sim("Produk apa aja?")
    pretty("GOLDEN: admin disabled skip", data)

    assert data["status"] == "skipped"
    assert data["source"] == "safety_policy"
    assert data["intent"] == "shop_admin_disabled"

    await reset_safety()


async def test_safety_policy_cases():
    await reset_safety()

    allowed = await evaluate_auto_reply_policy(
        shop_id=SHOP_ID,
        reply_result={
            "intent": "price_inquiry",
            "confidence": "high",
            "handoff_required": False,
        },
        channel="whatsapp",
        require_provider_ready=True,
    )
    pretty("GOLDEN: policy allowed", allowed)

    assert allowed["allowed"] is True
    assert allowed["action"] == "send_auto_reply"

    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {"$set": {"mode": "draft_only"}},
    )

    draft = await evaluate_auto_reply_policy(
        shop_id=SHOP_ID,
        reply_result={
            "intent": "price_inquiry",
            "confidence": "high",
            "handoff_required": False,
        },
        channel="whatsapp",
        require_provider_ready=False,
    )
    pretty("GOLDEN: policy draft_only", draft)

    assert draft["allowed"] is False
    assert draft["action"] == "draft_only"

    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {"$set": {"mode": "auto_reply"}},
    )

    handoff = await evaluate_auto_reply_policy(
        shop_id=SHOP_ID,
        reply_result={
            "intent": "complaint",
            "confidence": "high",
            "handoff_required": False,
        },
        channel="whatsapp",
        require_provider_ready=False,
    )
    pretty("GOLDEN: policy sensitive intent", handoff)

    assert handoff["allowed"] is False
    assert handoff["action"] == "handoff_required"

    await reset_safety()


async def main():
    print("Running Lapakin Asisten Golden Test Suite")
    print(f"BASE_URL={BASE_URL}")
    print(f"SHOP_ID={SHOP_ID}")

    status, health = http("GET", "/health")
    pretty("Health", health)
    assert_status(status, health, "health")

    await seed_golden_shop()
    await reset_safety()

    tests = [
        test_product_list,
        test_product_memory_total,
        test_payment,
        test_unknown_product_no_price_hallucination,
        test_handoff_keyword,
        test_global_maintenance_skip,
        test_admin_disabled_skip,
        test_safety_policy_cases,
    ]

    passed = 0

    for test in tests:
        name = test.__name__
        try:
            await test()
            passed += 1
            print(f"\n✅ {name}")
        except Exception as e:
            print(f"\n❌ {name}: {e}")
            raise

    pretty("SUMMARY", {
        "ok": True,
        "passed": passed,
        "total": len(tests),
        "shop_id": SHOP_ID,
    })


if __name__ == "__main__":
    asyncio.run(main())
