# app/routes/webhook.py
import base64
import hmac
import hashlib
import logging
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

LINQ_BASE = "https://api.linqapp.com/api/partner/v3"


def _verify_signature(
    body: bytes, msg_id: str | None, timestamp: str | None, signature: str | None
) -> bool:
    if not settings.linq_webhook_secret:
        return False
    if not (msg_id and timestamp and signature):
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False  # replay protection: reject anything older than 5 minutes
    except (ValueError, OverflowError):
        return False  # malformed timestamp: fail closed, not a 500

    secret = settings.linq_webhook_secret.removeprefix("whsec_")
    key = base64.b64decode(secret)
    signed_content = f"{msg_id}.{timestamp}.{body.decode()}"
    expected = base64.b64encode(
        hmac.new(key, signed_content.encode(), hashlib.sha256).digest()
    ).decode()

    for sig in signature.split(" "):
        if sig.startswith("v1,") and hmac.compare_digest(expected, sig[3:]):
            return True
    return False


async def send_text(chat_id: str, text: str) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{LINQ_BASE}/chats/{chat_id}/messages",
            headers={"Authorization": f"Bearer {settings.linq_api_token}"},
            json={"message": {"parts": [{"type": "text", "value": text}]}},
            timeout=15,
        )


def _extract_text(parts: list[dict]) -> str:
    for part in parts:
        if part.get("type") == "text":
            return part.get("value", "")
    return ""


def _extract_media_url(parts: list[dict]) -> str | None:
    for part in parts:
        if part.get("type") == "media":
            return part.get("url")
    return None


_AFFIRM_REACTIONS = frozenset({"like", "love", "emphasize"})
_DECLINE_REACTIONS = frozenset({"dislike"})
_AFFIRM_EMOJI = frozenset({"👍", "❤️", "♥️", "✅", "💯"})
_DECLINE_EMOJI = frozenset({"👎"})


def _reaction_to_intent(reaction_type: str | None, custom_emoji: str | None) -> str | None:
    """Map an iMessage tapback to the same yes/no intents as typed replies."""
    if reaction_type in _AFFIRM_REACTIONS:
        return "yes"
    if reaction_type in _DECLINE_REACTIONS:
        return "no"
    if reaction_type == "custom" and custom_emoji:
        if custom_emoji in _AFFIRM_EMOJI:
            return "yes"
        if custom_emoji in _DECLINE_EMOJI:
            return "no"
    return None


async def _handle_message_received(payload: dict) -> None:
    data = payload["data"]
    chat_id = data["chat"]["id"]
    phone = data["sender_handle"]["handle"]
    parts = data.get("parts", [])
    logger.info(
        "Received message parts: %s",
        [
            {
                "type": part.get("type"),
                "keys": sorted(part.keys()),
            }
            if isinstance(part, dict)
            else {"type": type(part).__name__, "keys": []}
            for part in parts
        ],
    )
    media_url = _extract_media_url(parts)
    if media_url:
        from app.orchestrator import handle_photo_message

        await handle_photo_message(phone, chat_id, media_url)
    else:
        text = _extract_text(parts)
        if text:
            from app.orchestrator import handle_text_message

            await handle_text_message(phone, chat_id, text)


async def _handle_reaction_added(payload: dict) -> None:
    data = payload.get("data") or {}
    if data.get("is_from_me"):
        return
    chat_id = data.get("chat_id")
    phone = data.get("from") or (data.get("from_handle") or {}).get("handle")
    if not chat_id or not phone:
        logger.warning("reaction.added missing chat_id/from: %s", list(data.keys()))
        return
    intent = _reaction_to_intent(data.get("reaction_type"), data.get("custom_emoji"))
    if intent is None:
        return
    logger.info(
        "Tapback checkout intent=%s type=%s chat=%s",
        intent,
        data.get("reaction_type"),
        chat_id,
    )
    from app.orchestrator import handle_text_message

    await handle_text_message(phone, chat_id, intent)


_seen_webhooks: dict[str, float] = {}
_WEBHOOK_DEDUP_SECONDS = 600


@router.post("/webhook/linq")
async def linq_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    webhook_id: str | None = Header(default=None),
    webhook_timestamp: str | None = Header(default=None),
    webhook_signature: str | None = Header(default=None),
):
    body = await request.body()
    if not _verify_signature(body, webhook_id, webhook_timestamp, webhook_signature):
        raise HTTPException(status_code=401, detail="bad signature")
    if webhook_id:
        now = time.time()
        stale = [
            wid
            for wid, seen_at in _seen_webhooks.items()
            if now - seen_at > _WEBHOOK_DEDUP_SECONDS
        ]
        for wid in stale:
            del _seen_webhooks[wid]
        if webhook_id in _seen_webhooks:
            return {"ok": True}
        _seen_webhooks[webhook_id] = now
    payload = await request.json()
    event_type = payload.get("event_type")
    logger.info(
        "Linq webhook event_type=%r keys=%s",
        event_type,
        sorted((payload.get("data") or {}).keys())
        if isinstance(payload.get("data"), dict)
        else None,
    )
    if event_type == "message.received":
        background_tasks.add_task(_handle_message_received, payload)
    elif event_type == "reaction.added":
        background_tasks.add_task(_handle_reaction_added, payload)
    else:
        logger.info("Ignoring Linq event_type=%r", event_type)
    return {"ok": True}
