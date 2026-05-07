#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
EMAIL = os.environ.get("EMAIL")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def http(method, path, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(BASE_URL + path, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read().decode("utf-8")
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return e.code, parsed


async def main():
    user_query = {"shop_id": {"$exists": True, "$ne": None}}
    if EMAIL:
        user_query["email"] = EMAIL

    user = await db.users.find_one(user_query, {"_id": 0})
    if not user:
        raise SystemExit("Owner user with shop_id not found")

    owner_token = create_access_token(user["user_id"], user["email"])

    admin_query = {"role": "admin"}
    if ADMIN_EMAIL:
        admin_query["email"] = ADMIN_EMAIL

    admin = await db.users.find_one(admin_query, {"_id": 0})
    if not admin:
        raise SystemExit("Admin user not found")

    admin_token = create_access_token(admin["user_id"], admin["email"])

    pretty("CONFIG", {
        "owner_email": user["email"],
        "shop_id": user["shop_id"],
        "admin_email": admin["email"],
    })

    status, data = http("GET", "/api/provider-readiness", token=owner_token)
    pretty(f"GET /api/provider-readiness -> {status}", data)
    assert status == 200
    assert "checks" in data
    assert "score" in data

    status, admin_data = http("GET", f"/api/admin/shops/{user['shop_id']}/provider-readiness", token=admin_token)
    pretty(f"GET /api/admin/shops/{user['shop_id']}/provider-readiness -> {status}", admin_data)
    assert status == 200
    assert admin_data["shop_id"] == user["shop_id"]

    print("\nOK provider readiness test passed")


if __name__ == "__main__":
    asyncio.run(main())
