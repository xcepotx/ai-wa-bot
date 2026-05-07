"""Effective business-hours / open-status layer for Lapakin Asisten.

Rules:
- source=lapakin: prefer LapakinUMKM context/status via lapakin_shop_id.
- source=standalone: use local bot_shop_profile / bot_settings.
- If explicit open/closed status is unavailable, parse simple business_hours text.
"""
from __future__ import annotations

import re
from datetime import datetime, time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from deps import db
from context_service import get_shop_context


DEFAULT_TIMEZONE = "Asia/Jakarta"

DAY_ALIASES = {
    "senin": 0, "mon": 0, "monday": 0,
    "selasa": 1, "tue": 1, "tuesday": 1,
    "rabu": 2, "wed": 2, "wednesday": 2,
    "kamis": 3, "thu": 3, "thursday": 3,
    "jumat": 4, "jum'at": 4, "friday": 4, "fri": 4,
    "sabtu": 5, "sat": 5, "saturday": 5,
    "minggu": 6, "ahad": 6, "sun": 6, "sunday": 6,
}

DAY_RANGE_WORDS = {
    "setiap hari": list(range(7)),
    "tiap hari": list(range(7)),
    "daily": list(range(7)),
    "everyday": list(range(7)),
}


def _get_path(data: dict, path: list[str]) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _truthy_text(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return bool(value)
    return bool(value)


def _shop_source(shop: dict) -> str:
    source = str(shop.get("status_source") or shop.get("source") or "standalone").lower()
    if source == "lapakin" or shop.get("lapakin_shop_id"):
        return "lapakin"
    return "local"


def _status_source_label(source: str) -> str:
    return "LapakinUMKM" if source == "lapakin" else "Lapakin Asisten"


def _normalize_open_status(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value

    if value is None:
        return None

    text = str(value).strip().lower()

    if text in {"open", "opened", "buka", "aktif", "online", "true", "1", "yes"}:
        return True

    if text in {"closed", "close", "tutup", "nonaktif", "offline", "false", "0", "no"}:
        return False

    return None


def _extract_business_hours_from_context(context: dict) -> Any:
    paths = [
        ["business_hours"],
        ["hours"],
        ["operational_hours"],
        ["opening_hours"],
        ["shop", "business_hours"],
        ["shop", "hours"],
        ["shop", "operational_hours"],
        ["shop", "opening_hours"],
        ["settings", "business_hours"],
        ["storefront_settings", "business_hours"],
        ["lapakin", "business_hours"],
        ["lapakin_context", "business_hours"],
    ]

    for path in paths:
        value = _get_path(context, path)
        if _truthy_text(value):
            return value

    return None


def _extract_open_status_from_context(context: dict) -> Optional[bool]:
    paths = [
        ["open_now"],
        ["is_open"],
        ["is_open_now"],
        ["shop", "open_now"],
        ["shop", "is_open"],
        ["shop", "is_open_now"],
        ["status"],
        ["shop", "status"],
        ["store_status"],
        ["shop", "store_status"],
        ["lapakin", "open_now"],
        ["lapakin_context", "open_now"],
    ]

    for path in paths:
        value = _get_path(context, path)
        normalized = _normalize_open_status(value)
        if normalized is not None:
            return normalized

    return None


def _extract_timezone(context: dict, shop: dict, profile_doc: dict, bot_settings: dict) -> str:
    candidates = [
        shop.get("timezone"),
        profile_doc.get("timezone") if profile_doc else None,
        bot_settings.get("timezone") if bot_settings else None,
        _get_path(context, ["timezone"]),
        _get_path(context, ["shop", "timezone"]),
        _get_path(context, ["settings", "timezone"]),
    ]

    for item in candidates:
        if item:
            return str(item)

    return DEFAULT_TIMEZONE


def _parse_time(text: str) -> Optional[time]:
    match = re.search(r"(\d{1,2})[.:](\d{2})", text)
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))

    if hour > 23 or minute > 59:
        return None

    return time(hour, minute)


def _parse_time_range(text: str) -> tuple[Optional[time], Optional[time]]:
    normalized = text.replace("–", "-").replace("—", "-").replace("s/d", "-").replace("sd", "-")
    parts = re.split(r"\s*-\s*", normalized)

    if len(parts) < 2:
        return None, None

    start = _parse_time(parts[0])
    end = _parse_time(parts[1])
    return start, end


def _parse_days(text: str) -> list[int]:
    low = text.lower()

    for phrase, days in DAY_RANGE_WORDS.items():
        if phrase in low:
            return days

    # Senin-Sabtu / Senin sampai Sabtu
    range_match = re.search(
        r"(senin|selasa|rabu|kamis|jumat|jum'at|sabtu|minggu|ahad|mon|tue|wed|thu|fri|sat|sun)\s*(?:-|sampai|sd|s/d|to)\s*(senin|selasa|rabu|kamis|jumat|jum'at|sabtu|minggu|ahad|mon|tue|wed|thu|fri|sat|sun)",
        low,
    )

    if range_match:
        start_day = DAY_ALIASES.get(range_match.group(1))
        end_day = DAY_ALIASES.get(range_match.group(2))

        if start_day is not None and end_day is not None:
            if start_day <= end_day:
                return list(range(start_day, end_day + 1))
            return list(range(start_day, 7)) + list(range(0, end_day + 1))

    found = []
    for name, index in DAY_ALIASES.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            found.append(index)

    return sorted(set(found))


def _compute_open_now_from_text(business_hours: Any, timezone: str) -> dict:
    """Best-effort parser for common simple strings.

    Supported examples:
    - "Senin-Sabtu 08.00-20.00"
    - "Setiap hari 09:00-21:00"
    - "08.00-20.00" means every day
    """
    if not isinstance(business_hours, str) or not business_hours.strip():
        return {
            "open_now": None,
            "status": "unknown",
            "status_label": "Belum diketahui",
            "reason": "business_hours kosong atau bukan teks",
        }

    text = business_hours.strip()
    low = text.lower()

    if "24 jam" in low or "24/7" in low:
        return {
            "open_now": True,
            "status": "open",
            "status_label": "Buka",
            "reason": "Jam operasional 24 jam",
        }

    days = _parse_days(text)
    if not days:
        days = list(range(7))

    start_time, end_time = _parse_time_range(text)
    if not start_time or not end_time:
        return {
            "open_now": None,
            "status": "unknown",
            "status_label": "Belum diketahui",
            "reason": "Format jam belum bisa diparse otomatis",
        }

    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
        timezone = DEFAULT_TIMEZONE

    today = now.weekday()
    current = now.time()

    if today not in days:
        return {
            "open_now": False,
            "status": "closed",
            "status_label": "Tutup",
            "reason": f"Hari ini di luar jadwal operasional ({business_hours})",
        }

    if start_time <= end_time:
        is_open = start_time <= current <= end_time
    else:
        # Overnight hours, example 20.00-02.00
        is_open = current >= start_time or current <= end_time

    return {
        "open_now": is_open,
        "status": "open" if is_open else "closed",
        "status_label": "Buka" if is_open else "Tutup",
        "reason": f"Dihitung dari business_hours: {business_hours}",
        "timezone": timezone,
        "today_weekday": today,
        "start_time": start_time.strftime("%H:%M"),
        "end_time": end_time.strftime("%H:%M"),
    }


async def get_effective_shop_status(
    shop_id: str,
    context: Optional[dict] = None,
    bot_settings: Optional[dict] = None,
    profile_doc: Optional[dict] = None,
) -> dict:
    if context is None:
        context = await get_shop_context(shop_id) or {}

    if bot_settings is None:
        bot_settings = await db.bot_settings.find_one({"shop_id": shop_id}, {"_id": 0}) or {}

    if profile_doc is None:
        profile_doc = await db.bot_shop_profile.find_one({"shop_id": shop_id}, {"_id": 0}) or {}

    shop = await db.shops.find_one({"shop_id": shop_id}, {"_id": 0}) or {}
    source = _shop_source(shop)

    timezone = _extract_timezone(context, shop, profile_doc or {}, bot_settings or {})

    # Prefer source-specific data.
    if source == "lapakin":
        business_hours = _extract_business_hours_from_context(context)
        explicit_open = _extract_open_status_from_context(context)

        # fallback if Lapakin context only has profile already mirrored
        if not _truthy_text(business_hours):
            business_hours = (
                shop.get("business_hours")
                or (profile_doc or {}).get("business_hours")
                or (bot_settings or {}).get("business_hours")
            )
    else:
        business_hours = (
            (profile_doc or {}).get("business_hours")
            or (bot_settings or {}).get("business_hours")
            or _extract_business_hours_from_context(context)
            or shop.get("business_hours")
        )
        explicit_open = (
            _normalize_open_status((profile_doc or {}).get("open_now"))
            if (profile_doc or {}).get("open_now") is not None
            else None
        )
        if explicit_open is None:
            explicit_open = _normalize_open_status((profile_doc or {}).get("status"))
        if explicit_open is None:
            explicit_open = _extract_open_status_from_context(context)

    parsed = _compute_open_now_from_text(business_hours, timezone)

    if explicit_open is not None:
        open_now = explicit_open
        status = "open" if open_now else "closed"
        status_label = "Buka" if open_now else "Tutup"
        reason = "Menggunakan status eksplisit dari source"
    else:
        open_now = parsed.get("open_now")
        status = parsed.get("status", "unknown")
        status_label = parsed.get("status_label", "Belum diketahui")
        reason = parsed.get("reason", "")

    return {
        "shop_id": shop_id,
        "shop_name": shop.get("name") or _get_path(context, ["shop", "name"]) or context.get("shop_name"),
        "source": source,
        "source_label": _status_source_label(source),
        "lapakin_shop_id": shop.get("lapakin_shop_id"),
        "timezone": timezone,
        "business_hours": business_hours,
        "business_hours_available": _truthy_text(business_hours),
        "open_now": open_now,
        "status": status,
        "status_label": status_label,
        "reason": reason,
        "computed": parsed,
    }


async def enrich_context_with_shop_status(shop_id: str, context: Optional[dict] = None) -> dict:
    if not isinstance(context, dict):
        context = {}

    enriched = dict(context)
    status = await get_effective_shop_status(shop_id, context=enriched)

    enriched["shop_status"] = status
    enriched["effective_shop_status"] = status

    if status.get("business_hours") and not enriched.get("business_hours"):
        enriched["business_hours"] = status.get("business_hours")

    enriched["open_now"] = status.get("open_now")
    enriched["open_status"] = status.get("status")
    enriched["open_status_label"] = status.get("status_label")

    return enriched
