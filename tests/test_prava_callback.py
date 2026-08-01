from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_callback_acks_immediately():
    resp = client.get("/prava/callback", params={"session_id": "nonexistent"})

    assert resp.status_code == 200
