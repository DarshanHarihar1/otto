from unittest.mock import AsyncMock, patch

from app.config import settings
from app.vision import Identification


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
    with (
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
    mock_download.assert_awaited_once_with("https://x/y.jpg")
    mock_archive.assert_called_once_with(item_id, b"bytes")
    mock_identify.assert_awaited_once_with(b"bytes")
    mock_send.assert_awaited_once_with(
        "chat1",
        "Got it — Minimalist Salicylic Acid 2% Serum. "
        "(Clear front label with brand and concentration visible.)",
    )
