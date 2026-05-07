#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "warungbusari@demo.lapakin.id")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def http(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

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


async def main():
    user = await db.users.find_one({"email": OWNER_EMAIL}, {"_id": 0})
    if not user:
        raise SystemExit(f"User not found: {OWNER_EMAIL}")

    token = create_access_token(user["user_id"], user["email"])

    pretty("CONFIG", {
        "email": user["email"],
        "shop_id": user.get("shop_id"),
    })

    status, suggested = http("POST", "/api/faqs/ai-suggest", token=token, body={
        "count": 8
    })
    pretty(f"POST /api/faqs/ai-suggest -> {status}", suggested)
    assert status == 200
    assert suggested["total"] >= 3
    assert len(suggested["items"]) >= 3

    items = suggested["items"][:3]

    status, created = http("POST", "/api/faqs/bulk-create", token=token, body={
        "items": [
            {"question": x["question"], "answer": x["answer"], "enabled": True}
            for x in items
        ]
    })
    pretty(f"POST /api/faqs/bulk-create -> {status}", created)
    assert status == 200
    assert created["ok"] is True

    print("\nOK AI FAQ generator test passed")


if __name__ == "__main__":
    asyncio.run(main())
