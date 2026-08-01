# tests/test_webhook.py
import base64
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routes.webhook import _extract_text, _handle_message_received

client = TestClient(app)

# Captured from a real Linq message.received webhook during live testing —
# event_type (not "event"), data.chat.id (not data.chat_id), text in
# data.parts[] (not data.text). See tests below for why this shape matters.
REAL_MESSAGE_RECEIVED_PAYLOAD = {
    "api_version": "v3",
    "webhook_version": "2026-02-03",
    "event_type": "message.received",
    "event_id": "06917325-f9e4-4234-807b-378ed4ae86e3",
    "data": {
        "chat": {"id": "6e4a83dc-ffba-4761-924c-5292fd8d84e3", "is_group": False},
        "direction": "inbound",
        "parts": [{"type": "text", "value": "hello otto"}],
        "sender_handle": {"handle": "+919900475117"},
        "service": "iMessage",
    },
}


def _sign(body: bytes, msg_id: str, timestamp: str) -> str:
    secret = settings.linq_webhook_secret.removeprefix("whsec_")
    key = base64.b64decode(secret)
    signed_content = f"{msg_id}.{timestamp}.{body.decode()}"
    digest = base64.b64encode(
        hmac.new(key, signed_content.encode(), hashlib.sha256).digest()
    ).decode()
    return f"v1,{digest}"


def test_rejects_bad_signature():
    body = json.dumps({"event": "message.received", "data": {}}).encode()
    resp = client.post(
        "/webhook/linq",
        content=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": str(int(time.time())),
            "webhook-signature": "v1,bad",
        },
    )
    assert resp.status_code == 401


def test_accepts_valid_signature():
    body = json.dumps(
        {"event": "message.received", "data": {"chat_id": "c1", "text": "hi"}}
    ).encode()
    timestamp = str(int(time.time()))
    resp = client.post(
        "/webhook/linq",
        content=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": timestamp,
            "webhook-signature": _sign(body, "msg_1", timestamp),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_rejects_malformed_timestamp():
    body = json.dumps({"event": "message.received", "data": {}}).encode()
    resp = client.post(
        "/webhook/linq",
        content=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": "not-a-number",
            "webhook-signature": "v1,bad",
        },
    )
    assert resp.status_code == 401


def test_rejects_stale_timestamp():
    body = json.dumps({"event": "message.received", "data": {}}).encode()
    stale_timestamp = str(int(time.time()) - 600)  # 10 minutes old
    resp = client.post(
        "/webhook/linq",
        content=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": stale_timestamp,
            "webhook-signature": _sign(body, "msg_1", stale_timestamp),
        },
    )
    assert resp.status_code == 401


def test_extract_text_finds_text_part():
    parts = [{"type": "text", "value": "hello otto"}]
    assert _extract_text(parts) == "hello otto"


def test_extract_text_returns_empty_for_no_text_part():
    assert _extract_text([]) == ""
    assert _extract_text([{"type": "media", "value": "https://..."}]) == ""


async def test_handle_message_received_parses_real_payload_shape():
    with patch("app.routes.webhook.send_text", AsyncMock()) as mock_send:
        await _handle_message_received(REAL_MESSAGE_RECEIVED_PAYLOAD)
    mock_send.assert_awaited_once_with(
        "6e4a83dc-ffba-4761-924c-5292fd8d84e3", "got it: hello otto"
    )
