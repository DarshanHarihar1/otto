# tests/test_webhook.py
import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


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
