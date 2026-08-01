# app/routes/webhook.py
import base64
import hmac
import hashlib
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.config import settings

router = APIRouter()

LINQ_BASE = "https://api.linqapp.com/api/partner/v3"


def _verify_signature(
    body: bytes, msg_id: str | None, timestamp: str | None, signature: str | None
) -> bool:
    if not (msg_id and timestamp and signature):
        return False
    if abs(time.time() - int(timestamp)) > 300:
        return False  # replay protection: reject anything older than 5 minutes

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
            json={"text": text},
            timeout=15,
        )


async def _handle_message_received(payload: dict) -> None:
    chat_id = payload["data"]["chat_id"]
    text = payload["data"].get("text", "")
    await send_text(chat_id, f"got it: {text}" if text else "got your message")


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
    payload = await request.json()
    if payload.get("event") == "message.received":
        background_tasks.add_task(_handle_message_received, payload)
    return {"ok": True}
