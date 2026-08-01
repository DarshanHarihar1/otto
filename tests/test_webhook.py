# tests/test_webhook.py
import base64
import dataclasses
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.routes.webhook import _extract_media_url, _extract_text, _handle_message_received

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


def test_message_received_dispatches_through_route():
    # Regression test for the bug fixed in 90c731b: the route checked
    # payload.get("event") but the real Linq field is "event_type", so
    # _handle_message_received was never dispatched for real messages.
    # This goes through the actual HTTP route (not calling the handler
    # directly) so it catches that class of bug again if reintroduced.
    body = json.dumps(REAL_MESSAGE_RECEIVED_PAYLOAD).encode()
    timestamp = str(int(time.time()))
    with patch("app.routes.webhook.send_text", AsyncMock()) as mock_send:
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
        mock_send.assert_awaited_once_with(
            "6e4a83dc-ffba-4761-924c-5292fd8d84e3", "got it: hello otto"
        )


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


def test_rejects_empty_webhook_secret_even_with_valid_signature():
    # Regression test: settings.linq_webhook_secret defaults to "" until the
    # Linq subscription exists (see app/config.py). base64.b64decode("") is a
    # valid (empty) HMAC key, so without an explicit guard, a signature
    # computed against an empty secret would still verify — fail-open. This
    # confirms _verify_signature fails closed when the secret is empty,
    # regardless of whether msg_id/timestamp/signature line up.
    body = json.dumps({"event": "message.received", "data": {}}).encode()
    timestamp = str(int(time.time()))
    empty_secret_settings = dataclasses.replace(settings, linq_webhook_secret="")
    with patch("app.routes.webhook.settings", empty_secret_settings):
        # Sign with the empty secret too, so this really is a "otherwise-valid"
        # signature for the (empty) key in effect — the guard, not a key
        # mismatch, must be what causes the rejection.
        key = base64.b64decode("")
        signed_content = f"msg_1.{timestamp}.{body.decode()}"
        digest = base64.b64encode(
            hmac.new(key, signed_content.encode(), hashlib.sha256).digest()
        ).decode()
        signature = f"v1,{digest}"
        resp = client.post(
            "/webhook/linq",
            content=body,
            headers={
                "webhook-id": "msg_1",
                "webhook-timestamp": timestamp,
                "webhook-signature": signature,
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


def test_extract_media_url_uses_url_key():
    parts = [
        {"type": "text", "value": "hello"},
        {"type": "media", "url": "https://cdn.linqapp.com/photo.jpg"},
    ]
    assert _extract_media_url(parts) == "https://cdn.linqapp.com/photo.jpg"


def test_extract_media_url_ignores_value_only_parts():
    parts = [{"type": "media", "value": "https://cdn.linqapp.com/other.jpg"}]
    assert _extract_media_url(parts) is None


async def test_handle_message_received_parses_real_payload_shape():
    with patch("app.routes.webhook.send_text", AsyncMock()) as mock_send:
        await _handle_message_received(REAL_MESSAGE_RECEIVED_PAYLOAD)
    mock_send.assert_awaited_once_with(
        "6e4a83dc-ffba-4761-924c-5292fd8d84e3", "got it: hello otto"
    )


async def test_handle_message_received_routes_media_part_to_orchestrator():
    payload = {
        **REAL_MESSAGE_RECEIVED_PAYLOAD,
        "data": {
            **REAL_MESSAGE_RECEIVED_PAYLOAD["data"],
            "parts": [
                {"type": "media", "url": "https://cdn.linqapp.com/photo.jpg"}
            ],
        },
    }

    with (
        patch(
            "app.orchestrator.handle_photo_message", AsyncMock()
        ) as mock_handle_photo,
        patch("app.routes.webhook.send_text", AsyncMock()) as mock_send,
    ):
        await _handle_message_received(payload)

    mock_handle_photo.assert_awaited_once_with(
        "+919900475117",
        "6e4a83dc-ffba-4761-924c-5292fd8d84e3",
        "https://cdn.linqapp.com/photo.jpg",
    )
    mock_send.assert_not_awaited()
