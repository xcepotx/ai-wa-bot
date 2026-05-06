"""Unified LLM client with provider fallback chain.

Provider order (priority):
  1) GEMINI_API_KEY  → Google Gemini direct (production-recommended, free tier)
  2) OPENAI_API_KEY  → OpenAI direct
  3) EMERGENT_LLM_KEY → Emergent Universal Key (works only inside Emergent platform)

Fallback behavior:
  - If a provider returns transient error (429 quota, 5xx upstream, network),
    automatically try the next provider in chain
  - If a provider returns non-transient error (401 auth, 400 bad request),
    log and also fallback (provider might be misconfigured)
  - All providers exhausted → raise last exception so caller can handle

Configure via `.env`:
    GEMINI_API_KEY=AIza...
    OPENAI_API_KEY=sk-...
    EMERGENT_LLM_KEY=sk-emergent-...

Users just set ANY ONE key. Adding multiple keys enables automatic failover.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("lapakin.llm")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY", "").strip()


def _providers_in_order() -> list:
    """Return list of available providers in priority order."""
    out = []
    if GEMINI_API_KEY:
        out.append("gemini")
    if OPENAI_API_KEY:
        out.append("openai")
    if EMERGENT_LLM_KEY:
        out.append("emergent")
    return out


def active_provider() -> str:
    """Return name of the primary LLM provider (first in chain)."""
    chain = _providers_in_order()
    return chain[0] if chain else "none"


def available_providers() -> list:
    """Public helper — returns ordered list of providers usable right now."""
    return _providers_in_order()


async def _log_event(kind: str, provider: str, detail: str = "") -> None:
    """Persist an LLM event (success / fallback / total_fail) for admin observability.
    Best-effort — never raises."""
    try:
        from deps import db  # lazy import to avoid circular
        from datetime import datetime, timezone
        await db.llm_events.insert_one({
            "kind": kind,            # success | fallback | total_fail
            "provider": provider,    # gemini | openai | emergent | <none>
            "detail": detail[:200],
            "at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        pass  # observability is never worth breaking the request over


async def chat_text(
    system: str,
    user: str,
    *,
    model_hint: str = "gemini-2.5-flash",
    session_id: Optional[str] = None,
) -> str:
    """Send a chat completion and return the raw text response.

    Tries providers in priority order. Falls back to next on any failure.
    Raises RuntimeError if no provider configured or all failed.
    """
    chain = _providers_in_order()
    if not chain:
        raise RuntimeError(
            "Tidak ada API key LLM yang tersedia. "
            "Set GEMINI_API_KEY, OPENAI_API_KEY, atau EMERGENT_LLM_KEY di .env"
        )

    last_err: Optional[Exception] = None
    for idx, provider in enumerate(chain):
        try:
            if provider == "gemini":
                text = await _chat_gemini(system, user, model_hint)
            elif provider == "openai":
                text = await _chat_openai(system, user, model_hint)
            elif provider == "emergent":
                text = await _chat_emergent(system, user, model_hint, session_id)
            else:
                continue
            if idx > 0:
                logger.info(
                    f"llm.chat_text — fell back to '{provider}' "
                    f"after '{chain[0]}' failed (attempt #{idx + 1})"
                )
                await _log_event("fallback", provider,
                                 f"primary={chain[0]} failed; used={provider}")
            await _log_event("success", provider)
            return text
        except Exception as e:
            last_err = e
            msg = str(e)[:200]
            logger.warning(
                f"llm.chat_text — provider '{provider}' failed: {msg}. "
                f"{'Trying next…' if idx < len(chain) - 1 else 'No more providers.'}"
            )
            continue

    # All providers exhausted
    assert last_err is not None
    await _log_event("total_fail", "none", str(last_err)[:200])
    raise RuntimeError(
        f"Semua provider LLM gagal. Terakhir: {type(last_err).__name__}: {last_err}"
    )

# ===================================================================
# IMAGE GENERATION (text-to-image & image editing)
# ===================================================================

async def chat_image_text2img(
    prompt: str,
    *,
    model_hint: str = "gemini-2.5-flash-image",
    session_id: Optional[str] = None,
) -> dict:
    """Generate an image from text prompt.

    Returns: {"data": <base64_string>, "mime_type": "image/png"}
    Raises RuntimeError if all providers fail.
    """
    chain = _providers_in_order()
    if not chain:
        raise RuntimeError(
            "Tidak ada API key LLM yang tersedia untuk image generation. "
            "Set GEMINI_API_KEY, OPENAI_API_KEY, atau EMERGENT_LLM_KEY di .env"
        )

    last_err: Optional[Exception] = None
    for idx, provider in enumerate(chain):
        try:
            if provider == "gemini":
                result = await _image_text2img_gemini(prompt)
            elif provider == "emergent":
                result = await _image_text2img_emergent(prompt, session_id)
            else:
                # OpenAI image gen (DALL-E) — skip for now, can be added later
                continue
            if idx > 0:
                logger.info(f"llm.chat_image_text2img — fell back to '{provider}'")
                await _log_event("fallback", provider, f"text2img: primary={chain[0]} failed")
            await _log_event("success", provider, "text2img")
            return result
        except Exception as e:
            last_err = e
            logger.warning(
                f"llm.chat_image_text2img — provider '{provider}' failed: {str(e)[:200]}"
            )
            continue

    assert last_err is not None
    await _log_event("total_fail", "none", f"text2img: {str(last_err)[:200]}")
    raise RuntimeError(f"Semua provider image gen gagal: {type(last_err).__name__}: {last_err}")


async def chat_image_edit(
    prompt: str,
    image_base64: str,
    *,
    model_hint: str = "gemini-2.5-flash-image",
    session_id: Optional[str] = None,
) -> dict:
    """Edit/transform an existing image based on prompt.

    Args:
        prompt: instruction text
        image_base64: input image as raw base64 string (no `data:` prefix)

    Returns: {"data": <base64_string>, "mime_type": "image/png"}
    """
    chain = _providers_in_order()
    if not chain:
        raise RuntimeError(
            "Tidak ada API key LLM yang tersedia untuk image edit."
        )

    # Strip data URL prefix if present
    if image_base64.startswith("data:"):
        image_base64 = image_base64.split(",", 1)[-1]

    last_err: Optional[Exception] = None
    for idx, provider in enumerate(chain):
        try:
            if provider == "gemini":
                result = await _image_edit_gemini(prompt, image_base64)
            elif provider == "emergent":
                result = await _image_edit_emergent(prompt, image_base64, session_id)
            else:
                continue
            if idx > 0:
                logger.info(f"llm.chat_image_edit — fell back to '{provider}'")
                await _log_event("fallback", provider, f"edit: primary={chain[0]} failed")
            await _log_event("success", provider, "edit")
            return result
        except Exception as e:
            last_err = e
            logger.warning(
                f"llm.chat_image_edit — provider '{provider}' failed: {str(e)[:200]}"
            )
            continue

    assert last_err is not None
    await _log_event("total_fail", "none", f"edit: {str(last_err)[:200]}")
    raise RuntimeError(f"Semua provider image edit gagal: {type(last_err).__name__}: {last_err}")


# ---------------- Image: Gemini implementations ----------------
async def _image_text2img_gemini(prompt: str) -> dict:
    """Generate image from text via Gemini 2.5 Flash Image."""
    import base64
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        ),
    )
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            raw_bytes = part.inline_data.data
            mime = part.inline_data.mime_type or "image/png"
            data_b64 = base64.b64encode(raw_bytes).decode("ascii")
            return {"data": data_b64, "mime_type": mime}
    raise RuntimeError("Gemini did not return any image part")


async def _image_edit_gemini(prompt: str, image_b64: str) -> dict:
    """Edit image via Gemini 2.5 Flash Image (multimodal input)."""
    import base64
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=GEMINI_API_KEY)
    img_bytes = base64.b64decode(image_b64)

    response = client.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[
            prompt,
            genai_types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
        ],
        config=genai_types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        ),
    )
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            raw = part.inline_data.data
            mime = part.inline_data.mime_type or "image/png"
            data_b64 = base64.b64encode(raw).decode("ascii")
            return {"data": data_b64, "mime_type": mime}
    raise RuntimeError("Gemini did not return any image part for edit")


# ---------------- Image: Emergent implementations ----------------
async def _image_text2img_emergent(prompt: str, session_id: Optional[str]) -> dict:
    """Fallback to Emergent (works only inside Emergent platform)."""
    import uuid
    from emergentintegrations.llm.chat import LlmChat, UserMessage

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id or f"img_{uuid.uuid4().hex[:8]}",
        system_message="You are a master commercial photographer.",
    )
    chat.with_model("gemini", "gemini-3.1-flash-image-preview").with_params(modalities=["image", "text"])
    text, images = await chat.send_message_multimodal_response(UserMessage(text=prompt))
    if not images:
        raise RuntimeError("Emergent did not return any image")
    return {"data": images[0]["data"], "mime_type": images[0].get("mime_type", "image/png")}


async def _image_edit_emergent(prompt: str, image_b64: str, session_id: Optional[str]) -> dict:
    """Fallback to Emergent for image edit."""
    import uuid
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent

    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id or f"edt_{uuid.uuid4().hex[:8]}",
        system_message="You are a world-class product photo retoucher.",
    )
    chat.with_model("gemini", "gemini-3.1-flash-image-preview").with_params(modalities=["image", "text"])
    msg = UserMessage(text=prompt, file_contents=[ImageContent(image_b64)])
    text, images = await chat.send_message_multimodal_response(msg)
    if not images:
        raise RuntimeError("Emergent did not return any image for edit")
    return {"data": images[0]["data"], "mime_type": images[0].get("mime_type", "image/png")}


# ---------------- Provider implementations ----------------
async def _chat_gemini(system: str, user: str, model_hint: str) -> str:
    """Direct Google Gemini API call. Free tier: 15 req/min."""
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=GEMINI_API_KEY)
    model = "gemini-2.5-flash"
    if "pro" in model_hint.lower():
        model = "gemini-2.5-pro"
    response = client.models.generate_content(
        model=model,
        contents=user,
        config=genai_types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="text/plain",
        ),
    )
    return response.text or ""


async def _chat_openai(system: str, user: str, model_hint: str) -> str:
    """Direct OpenAI API call."""
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    model = "gpt-4o-mini" if ("flash" in model_hint or "mini" in model_hint) else "gpt-4o"
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


async def _chat_emergent(system: str, user: str, model_hint: str, session_id: Optional[str]) -> str:
    """Emergent Universal Key — works only inside Emergent preview environment."""
    import uuid
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id or f"sess_{uuid.uuid4().hex[:8]}",
        system_message=system,
    )
    if "gemini" in model_hint:
        chat = chat.with_model("gemini", "gemini-2.5-flash")
    else:
        chat = chat.with_model("openai", "gpt-4o-mini")
    return await chat.send_message(UserMessage(text=user))
