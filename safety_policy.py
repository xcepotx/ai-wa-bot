"""Auto-reply safety policy for Lapakin Asisten.

This module decides whether a generated reply may be sent automatically
to a real provider such as WhatsApp.

Provider adapter flow should be:

inbound message
-> generate reply
-> evaluate_auto_reply_policy(...)
-> if allowed: send_auto_reply
-> else: draft/handoff/skipped
"""

from typing import Any, Dict, Optional

from deps import db, now_iso, new_id


AUTO_REPLY_MODES = {"auto_reply", "auto_reply_with_handoff"}
SENSITIVE_INTENTS = {
    "complaint",
    "refund",
    "return",
    "cancel_order",
    "human_request",
    "handoff_request",
    "admin_request",
    "owner_request",
}
LOW_CONFIDENCE_VALUES = {"low", "very_low", "0", "0.0"}


async def evaluate_auto_reply_policy(
    shop_id: str,
    session_id: Optional[str] = None,
    reply_result: Optional[Dict[str, Any]] = None,
    channel: str = "whatsapp",
    require_provider_ready: bool = True,
    write_event: bool = False,
) -> Dict[str, Any]:
    """Evaluate if a reply can be sent automatically.

    Returns:
      {
        allowed: bool,
        action: send_auto_reply | draft_only | handoff_required | skipped_*,
        status: sent | draft_only | handoff | skipped,
        reason: str,
        checks: [...]
      }
    """
    reply_result = reply_result or {}
    checks = []

    async def fail(action: str, status: str, reason: str, code: str):
        result = {
            "allowed": False,
            "action": action,
            "status": status,
            "reason": reason,
            "code": code,
            "checks": checks,
            "channel": channel,
        }
        if write_event:
            await write_policy_event(shop_id, session_id, result)
        return result

    def add_check(key: str, passed: bool, detail: str = ""):
        checks.append({
            "key": key,
            "passed": bool(passed),
            "detail": detail,
        })

    control = await db.system_settings.find_one(
        {"key": "lapakin_asisten_control"},
        {"_id": 0},
    ) or {"status": "on"}

    system_status = control.get("status", "on")
    add_check("global_system_on", system_status == "on", f"status={system_status}")

    if system_status != "on":
        reason = control.get("reason") or "Global Lapakin Asisten sedang tidak aktif."
        return await fail(
            action="skipped_global_disabled",
            status="skipped",
            reason=reason,
            code="GLOBAL_DISABLED",
        )

    settings = await db.bot_settings.find_one(
        {"shop_id": shop_id},
        {"_id": 0},
    ) or {}

    admin_disabled = bool(settings.get("admin_disabled"))
    add_check("shop_not_admin_disabled", not admin_disabled, settings.get("admin_disable_reason") or "")

    if admin_disabled:
        return await fail(
            action="skipped_shop_disabled",
            status="skipped",
            reason=settings.get("admin_disable_reason") or "Toko dinonaktifkan admin.",
            code="SHOP_ADMIN_DISABLED",
        )

    enabled = bool(settings.get("enabled"))
    mode = settings.get("mode") or "off"

    add_check("assistant_enabled", enabled, f"enabled={enabled}")
    add_check("mode_allows_auto_reply", mode in AUTO_REPLY_MODES, f"mode={mode}")

    if not enabled or mode == "off":
        return await fail(
            action="draft_only",
            status="draft_only",
            reason="Lapakin Asisten belum aktif untuk toko ini.",
            code="ASSISTANT_DISABLED",
        )

    if mode == "draft_only":
        return await fail(
            action="draft_only",
            status="draft_only",
            reason="Mode toko masih draft_only, jadi reply tidak dikirim otomatis.",
            code="DRAFT_ONLY_MODE",
        )

    if mode not in AUTO_REPLY_MODES:
        return await fail(
            action="draft_only",
            status="draft_only",
            reason=f"Mode {mode} belum diizinkan untuk auto-reply.",
            code="MODE_NOT_ALLOWED",
        )

    if require_provider_ready:
        try:
            from routes.provider_readiness import calculate_provider_readiness

            readiness = await calculate_provider_readiness(shop_id)
            provider_ready = bool(readiness.get("provider_ready"))
            score = readiness.get("score")
            status = readiness.get("status")

            add_check(
                "provider_ready",
                provider_ready,
                f"score={score}, status={status}",
            )

            if not provider_ready:
                return await fail(
                    action="skipped_readiness",
                    status="skipped",
                    reason=f"Provider readiness belum siap. Score: {score}/100, status: {status}.",
                    code="PROVIDER_NOT_READY",
                )
        except Exception as e:
            return await fail(
                action="skipped_readiness",
                status="skipped",
                reason=f"Gagal mengecek provider readiness: {e}",
                code="READINESS_CHECK_FAILED",
            )

    quota_monthly = settings.get("quota_monthly")
    quota_used = settings.get("quota_used", 0)

    if quota_monthly is not None:
        try:
            quota_monthly_num = int(quota_monthly)
            quota_used_num = int(quota_used or 0)
            quota_ok = quota_used_num < quota_monthly_num
            add_check("quota_available", quota_ok, f"{quota_used_num}/{quota_monthly_num}")

            if not quota_ok:
                return await fail(
                    action="skipped_quota",
                    status="skipped",
                    reason="Kuota auto-reply toko sudah habis.",
                    code="QUOTA_EXCEEDED",
                )
        except Exception:
            add_check("quota_available", True, "quota invalid, ignored")

    intent = str(reply_result.get("intent") or "").lower()
    confidence = str(reply_result.get("confidence") or "").lower()
    handoff_required = bool(reply_result.get("handoff_required"))

    sensitive_intent = intent in SENSITIVE_INTENTS
    low_confidence = confidence in LOW_CONFIDENCE_VALUES

    add_check("not_sensitive_intent", not sensitive_intent, f"intent={intent}")
    add_check("handoff_not_required", not handoff_required, f"handoff_required={handoff_required}")
    add_check("confidence_ok", not low_confidence, f"confidence={confidence}")

    if sensitive_intent:
        return await fail(
            action="handoff_required",
            status="handoff",
            reason=f"Intent {intent} wajib ditangani owner/admin.",
            code="SENSITIVE_INTENT",
        )

    if handoff_required:
        return await fail(
            action="handoff_required",
            status="handoff",
            reason="Reply engine menandai percakapan perlu handoff.",
            code="HANDOFF_REQUIRED",
        )

    if low_confidence:
        return await fail(
            action="skipped_low_confidence",
            status="draft_only",
            reason="Confidence rendah, reply disimpan sebagai draft.",
            code="LOW_CONFIDENCE",
        )

    result = {
        "allowed": True,
        "action": "send_auto_reply",
        "status": "sent",
        "reason": "Auto-reply allowed.",
        "code": "AUTO_REPLY_ALLOWED",
        "checks": checks,
        "channel": channel,
    }

    if write_event:
        await write_policy_event(shop_id, session_id, result)

    return result


async def write_policy_event(
    shop_id: str,
    session_id: Optional[str],
    policy_result: Dict[str, Any],
):
    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "policy.evaluated",
        "payload": {
            "session_id": session_id,
            "allowed": policy_result.get("allowed"),
            "action": policy_result.get("action"),
            "status": policy_result.get("status"),
            "reason": policy_result.get("reason"),
            "code": policy_result.get("code"),
            "channel": policy_result.get("channel"),
        },
        "created_at": now_iso(),
    })


async def mark_auto_reply_sent(shop_id: str, session_id: Optional[str] = None):
    """Increment quota after a real provider send succeeds."""
    now = now_iso()

    await db.bot_settings.update_one(
        {"shop_id": shop_id},
        {
            "$inc": {"quota_used": 1},
            "$set": {"last_auto_reply_sent_at": now, "updated_at": now},
        },
    )

    await db.bot_events.insert_one({
        "event_id": new_id("evt"),
        "shop_id": shop_id,
        "type": "reply.sent_auto",
        "payload": {
            "session_id": session_id,
        },
        "created_at": now,
    })
