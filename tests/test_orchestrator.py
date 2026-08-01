import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import settings
from app.vision import Identification


def _mock_db():
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("user-uuid-1",)

    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__.return_value = mock_cur
    mock_cursor_cm.__exit__.return_value = False

    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor_cm

    mock_conn_cm = MagicMock()
    mock_conn_cm.__enter__.return_value = mock_conn
    mock_conn_cm.__exit__.return_value = False

    mock_get_conn = MagicMock(return_value=mock_conn_cm)
    return mock_get_conn, mock_cur


async def test_handle_photo_message_creates_and_updates_item():
    from app.orchestrator import handle_photo_message

    fake_result = Identification(
        object_type="serum bottle",
        brand="Minimalist",
        product="Salicylic Acid 2% Serum",
        variant="30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"],
        confidence=0.95,
        reasoning="Clear front label with brand and concentration visible.",
        missing_info=None,
        suggested_photo=None,
    )
    mock_get_conn, mock_cur = _mock_db()
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.download_media", AsyncMock(return_value=b"bytes")
        ) as mock_download,
        patch("app.orchestrator.archive_photo", return_value="path.jpg") as mock_archive,
        patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)) as mock_identify,
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        item_id = await handle_photo_message(
            settings.demo_user_phone, "chat1", "https://x/y.jpg"
        )

    assert item_id
    assert mock_get_conn.call_count == 2
    mock_download.assert_awaited_once_with("https://x/y.jpg")
    mock_archive.assert_called_once_with(item_id, b"bytes")
    mock_identify.assert_awaited_once_with(b"bytes")
    mock_send.assert_awaited_once_with(
        "chat1",
        "Got it — Minimalist Salicylic Acid 2% Serum. "
        "(Clear front label with brand and concentration visible.)",
    )

    calls = mock_cur.execute.call_args_list
    assert calls[0][0][0].strip().startswith("SELECT id FROM users")
    assert calls[0][0][1] == (settings.demo_user_phone,)

    insert_sql, insert_params = calls[1][0]
    assert "INSERT INTO items" in insert_sql
    assert "IDENTIFYING" in insert_sql
    assert insert_params[0] == item_id
    assert insert_params[1] == "user-uuid-1"

    update_sql, update_params = calls[2][0]
    assert "UPDATE items" in update_sql
    assert update_params[:6] == (
        "Minimalist",
        "Salicylic Acid 2% Serum",
        "30ml",
        "Beauty & Personal Care/Skin Care",
        0.95,
        "path.jpg",
    )
    assert update_params[6] == item_id

    event_sql, event_params = calls[3][0]
    assert "INSERT INTO events" in event_sql
    assert "identified" in event_sql
    assert event_params[0] == item_id
    event_payload = json.loads(event_params[1])
    assert event_payload["brand"] == "Minimalist"
    assert event_payload["product"] == "Salicylic Acid 2% Serum"
    assert event_payload["confidence"] == 0.95


async def test_handle_photo_message_replies_when_user_is_unknown():
    from app.orchestrator import handle_photo_message

    mock_get_conn, mock_cur = _mock_db()
    mock_cur.fetchone.return_value = None
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        item_id = await handle_photo_message("+910000000000", "chat1", "https://x/y.jpg")

    assert item_id is None
    mock_send.assert_awaited_once_with(
        "chat1", "Couldn't read that one — try a photo of the front label?"
    )


async def test_handle_photo_message_replies_when_identification_is_missing():
    from app.orchestrator import handle_photo_message

    mock_get_conn, mock_cur = _mock_db()
    with (
        patch("app.orchestrator.get_conn", mock_get_conn),
        patch(
            "app.orchestrator.download_media", AsyncMock(return_value=b"bytes")
        ),
        patch("app.orchestrator.archive_photo", return_value="path.jpg"),
        patch("app.orchestrator.identify", AsyncMock(return_value=None)),
        patch("app.orchestrator.send_text", AsyncMock()) as mock_send,
    ):
        item_id = await handle_photo_message(
            settings.demo_user_phone, "chat1", "https://x/y.jpg"
        )

    assert item_id is None
    mock_send.assert_awaited_once_with(
        "chat1", "Couldn't read that one — try a photo of the front label?"
    )
    assert mock_cur.execute.call_count == 2
