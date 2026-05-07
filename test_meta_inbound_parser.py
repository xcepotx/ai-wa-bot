#!/usr/bin/env python3
import os
import json
import time
import asyncio
import urllib.request
import urllib.error

from deps import db
from test_golden_suite import seed_golden_shop, reset_safety, SHOP_ID

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8002").rstrip("/")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "meta_phone_golden_001")
WABA_ID = os.environ.get("WABA_ID", "meta_waba_golden_001")


def pretty(title, data):
    print("\\n" + "=" * 88)
    print(title)
    print("=" * 88)
    print(json.dumps(data, indent=2, ensure_ascii=False))


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


def meta_payload(message_id, from_phone, body_text):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+628000000000",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Meta Budi"},
                                    "wa_id": from_phone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": message_id,
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {
                                        "body": body_text,
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def meta_status_payload(wamid, status="delivered"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": WABA_ID,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+628000000000",
                                "phone_number_id": PHONE_NUMBER_ID,
                            },
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": status,
                                    "timestamp": str(int(time.time())),
                                    "recipient_id": "628555001000",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


async def main():
    status, health = http("GET", "/health")
    pretty("Health", health)
    assert status == 200

    await seed_golden_shop()
    await reset_safety()

    await db.bot_settings.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "enabled": True,
                "mode": "auto_reply",
                "admin_disabled": False,
                "quota_monthly": 100,
                "quota_used": 0,
            }
        },
        upsert=True,
    )

    await db.provider_credentials.update_one(
        {"shop_id": SHOP_ID},
        {
            "$set": {
                "shop_id": SHOP_ID,
                "provider": "meta_cloud",
                "status": "configured",
                "enabled": False,
                "phone_number_id": PHONE_NUMBER_ID,
                "waba_id": WABA_ID,
                "verify_token": "verify_meta_parser_test",
                "updated_at": "test",
            },
            "$setOnInsert": {
                "credential_id": "cred_meta_parser_test",
                "created_at": "test",
            },
        },
        upsert=True,
    )

    from_phone = "628555001000"
    msg1 = "wamid.meta.test." + str(int(time.time())) + ".1"
    msg2 = "wamid.meta.test." + str(int(time.time())) + ".2"

    status, first = http("POST", "/api/provider/meta/webhook", meta_payload(
        msg1,
        from_phone,
        "Harga Nasi Goreng berapa?"
    ))
    pretty(f"META WEBHOOK #1 -> {status}", first)
    assert status == 200
    assert first["counts"]["messages"] == 1

    item1 = first["processed_messages"][0]
    assert item1["ok"] is True
    assert item1["provider_status"] == "pending_send"
    assert item1["policy"]["allowed"] is True
    assert "Nasi Goreng" in item1["bot_reply"]

    status, dup = http("POST", "/api/provider/meta/webhook", meta_payload(
        msg1,
        from_phone,
        "Harga Nasi Goreng berapa?"
    ))
    pretty(f"META WEBHOOK DUPLICATE -> {status}", dup)
    assert status == 200
    assert dup["processed_messages"][0]["duplicate"] is True

    status, second = http("POST", "/api/provider/meta/webhook", meta_payload(
        msg2,
        from_phone,
        "Kalau 2 berapa?"
    ))
    pretty(f"META WEBHOOK #2 FOLLOW-UP -> {status}", second)
    assert status == 200

    item2 = second["processed_messages"][0]
    assert item2["session_id"] == item1["session_id"]
    assert item2["provider_status"] == "pending_send"
    assert "Rp30.000" in item2["bot_reply"]

    status, status_res = http("POST", "/api/provider/meta/webhook", meta_status_payload("wamid.fake.outbound", "delivered"))
    pretty(f"META STATUS WEBHOOK -> {status}", status_res)
    assert status == 200
    assert status_res["counts"]["statuses"] == 1

    session = await db.sessions.find_one({"session_id": item2["session_id"]}, {"_id": 0})
    provider_messages = await db.provider_messages.find(
        {"session_id": item2["session_id"], "provider": "meta_cloud"},
        {"_id": 0, "direction": 1, "status": 1, "provider_message_id": 1, "text": 1, "created_at": 1}
    ).sort("created_at", 1).to_list(20)

    events = await db.bot_events.find(
        {"payload.session_id": item2["session_id"]},
        {"_id": 0, "type": 1, "payload": 1, "created_at": 1}
    ).sort("created_at", 1).to_list(50)

    pretty("SESSION DB CHECK", session)
    pretty("PROVIDER MESSAGES DB CHECK", provider_messages)
    pretty("EVENTS DB CHECK", events)

    assert session["source"] == "whatsapp"
    assert session["provider"] == "meta_cloud"
    assert session["provider_status"] == "pending_send"
    assert len(provider_messages) >= 4

    print("\\nOK meta inbound parser test passed")


if __name__ == "__main__":
    asyncio.run(main())
