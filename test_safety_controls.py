#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL")
SHOP_ID = os.environ.get("SHOP_ID")


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


async def main():
    admin_query = {"role": "admin"}
    if ADMIN_EMAIL:
        admin_query["email"] = ADMIN_EMAIL

    admin = await db.users.find_one(admin_query, {"_id": 0})
    if not admin:
        raise SystemExit("Admin user not found. Set role admin first.")

    shop_id = SHOP_ID
    if not shop_id:
        shop = await db.shops.find_one({}, {"_id": 0, "shop_id": 1, "name": 1})
        if not shop:
            raise SystemExit("No shop found.")
        shop_id = shop["shop_id"]

    token = create_access_token(admin["user_id"], admin["email"])

    pretty("CONFIG", {
        "admin_email": admin["email"],
        "shop_id": shop_id,
    })

    status, data = http("GET", "/api/admin/system-status", token=token)
    pretty(f"GET system-status -> {status}", data)
    assert status == 200

    status, data = http("PUT", "/api/admin/system-status", token=token, body={
        "status": "maintenance",
        "reason": "Test maintenance kill switch"
    })
    pretty(f"PUT maintenance -> {status}", data)
    assert status == 200

    status, sim = http("POST", "/api/simulate", body={
        "shop_id": shop_id,
        "customer_message": "Produk apa aja?",
        "customer_name": "Safety Tester"
    })
    pretty(f"SIM while maintenance -> {status}", sim)
    assert status == 200
    assert sim.get("status") == "skipped"
    assert sim.get("source") == "safety_policy"

    status, data = http("PUT", "/api/admin/system-status", token=token, body={
        "status": "on",
        "reason": ""
    })
    pretty(f"PUT on -> {status}", data)
    assert status == 200

    status, data = http("POST", f"/api/admin/shops/{shop_id}/force-disable", token=token, body={
        "reason": "Test force disable shop"
    })
    pretty(f"POST force-disable -> {status}", data)
    assert status == 200

    status, sim = http("POST", "/api/simulate", body={
        "shop_id": shop_id,
        "customer_message": "Produk apa aja?",
        "customer_name": "Safety Tester"
    })
    pretty(f"SIM while shop disabled -> {status}", sim)
    assert status == 200
    assert sim.get("status") == "skipped"
    assert sim.get("source") == "safety_policy"

    status, data = http("POST", f"/api/admin/shops/{shop_id}/force-enable", token=token, body={
        "reason": "Test restore shop"
    })
    pretty(f"POST force-enable -> {status}", data)
    assert status == 200

    status, data = http("GET", "/api/admin/shops", token=token)
    pretty(f"GET admin shops -> {status}", data)
    assert status == 200

    print("\nOK safety control test passed")


if __name__ == "__main__":
    asyncio.run(main())
