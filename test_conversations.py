#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
EMAIL = os.environ.get("warungbusari@demo.lapakin.my.id")
SHOP_ID = os.environ.get("shop_290a508e7c59")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(data)


def http(method, path, token=None, body=None):
    url = BASE_URL + path
    headers = {
        "Content-Type": "application/json",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8")
            try:
                return res.status, json.loads(raw)
            except Exception:
                return res.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed
    except Exception as e:
        return 0, {"error": str(e)}


async def pick_user():
    query = {}

    if EMAIL:
        query["email"] = EMAIL

    if not EMAIL:
        query = {"shop_id": {"$exists": True, "$ne": None}}

    user = await db.users.find_one(query, {"_id": 0, "password_hash": 0})

    if not user:
        raise SystemExit(
            "User tidak ditemukan. Coba set EMAIL=... atau pastikan ada user dengan shop_id."
        )

    if not user.get("shop_id") and not SHOP_ID:
        raise SystemExit(
            f"User {user.get('email')} tidak punya shop_id. Set SHOP_ID=... manual."
        )

    return user


async def main():
    user = await pick_user()

    shop_id = SHOP_ID or user["shop_id"]
    token = create_access_token(user["user_id"], user["email"])

    pretty("CONFIG", {
        "base_url": BASE_URL,
        "email": user.get("email"),
        "user_id": user.get("user_id"),
        "shop_id": shop_id,
        "role": user.get("role"),
        "token_preview": token[:30] + "...",
    })

    status, health = http("GET", "/health")
    pretty(f"GET /health -> {status}", health)
    if status != 200:
        raise SystemExit("Health check gagal. Pastikan service ai-wa-bot jalan.")

    first_message = os.environ.get("MSG1", "Produk apa aja yang tersedia?")
    second_message = os.environ.get("MSG2", "Kalau saya mau pesan 2, gimana?")

    status, sim1 = http("POST", "/api/simulate", body={
        "shop_id": shop_id,
        "customer_message": first_message,
        "customer_name": "Tester Script",
        "customer_phone": "+6281111111111",
    })
    pretty(f"POST /api/simulate #1 -> {status}", sim1)
    if status != 200:
        raise SystemExit("Simulate #1 gagal.")

    session_id = sim1.get("session_id")
    if not session_id:
        raise SystemExit("Response simulate tidak punya session_id.")

    status, sim2 = http("POST", "/api/simulate", body={
        "shop_id": shop_id,
        "session_id": session_id,
        "customer_message": second_message,
        "customer_name": "Tester Script",
        "customer_phone": "+6281111111111",
    })
    pretty(f"POST /api/simulate #2 -> {status}", sim2)
    if status != 200:
        raise SystemExit("Simulate #2 gagal.")

    status, conversations = http("GET", "/api/conversations", token=token)
    pretty(f"GET /api/conversations -> {status}", conversations)
    if status != 200:
        raise SystemExit("GET /api/conversations gagal.")

    status, detail = http("GET", f"/api/conversations/{session_id}", token=token)
    pretty(f"GET /api/conversations/{session_id} -> {status}", detail)
    if status != 200:
        raise SystemExit("GET conversation detail gagal.")

    messages = detail.get("messages", [])
    if len(messages) < 4:
        raise SystemExit(
            f"Expected minimal 4 messages dari 2 simulasi, dapat {len(messages)}."
        )

    status, handoff = http("POST", f"/api/conversations/{session_id}/handoff", token=token, body={
        "note": "Test script: tandai handoff."
    })
    pretty(f"POST /api/conversations/{session_id}/handoff -> {status}", handoff)
    if status != 200:
        raise SystemExit("POST handoff gagal.")

    status, resolved = http("POST", f"/api/conversations/{session_id}/resolve", token=token, body={
        "note": "Test script: resolved."
    })
    pretty(f"POST /api/conversations/{session_id}/resolve -> {status}", resolved)
    if status != 200:
        raise SystemExit("POST resolve gagal.")

    status, final_detail = http("GET", f"/api/conversations/{session_id}", token=token)
    pretty(f"FINAL GET /api/conversations/{session_id} -> {status}", final_detail)

    final_status = final_detail.get("session", {}).get("status")
    if final_status != "resolved":
        raise SystemExit(f"Expected final status resolved, dapat: {final_status}")

    pretty("RESULT", {
        "ok": True,
        "session_id": session_id,
        "final_status": final_status,
        "message_count": len(final_detail.get("messages", [])),
    })


if __name__ == "__main__":
    asyncio.run(main())
