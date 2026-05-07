#!/usr/bin/env python3
import os
import json
import asyncio
import urllib.request
import urllib.error

from deps import db, create_access_token


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
EMAIL = os.environ.get("ADMIN_EMAIL")


def pretty(title, data):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def http(method, path, token=None, body=None):
    url = BASE_URL + path
    headers = {"Content-Type": "application/json"}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)

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
    except Exception as e:
        return 0, {"error": str(e)}


async def pick_admin():
    query = {"role": "admin"}
    if EMAIL:
        query["email"] = EMAIL

    user = await db.users.find_one(query, {"_id": 0, "password_hash": 0})
    if not user:
        raise SystemExit(
            "Admin user tidak ditemukan. Jalankan update role admin dulu atau set ADMIN_EMAIL=..."
        )
    return user


async def main():
    admin = await pick_admin()
    token = create_access_token(admin["user_id"], admin["email"])

    pretty("CONFIG", {
        "base_url": BASE_URL,
        "admin_email": admin["email"],
        "admin_user_id": admin["user_id"],
    })

    for path in ["/api/admin/overview", "/api/admin/shops", "/api/admin/conversations"]:
        status, data = http("GET", path, token=token)
        pretty(f"GET {path} -> {status}", data)
        if status != 200:
            raise SystemExit(f"{path} failed")

    print("\nOK admin endpoint test passed")


if __name__ == "__main__":
    asyncio.run(main())
