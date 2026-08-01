from unittest.mock import AsyncMock, patch

from app.reply_composer import compose_and_send
from app.state_machine import ItemState
from app.vision import Identification


async def test_needs_angle_asks_specific_angle():
    result = Identification(
        object_type="bottle", brand="Minimalist", product="Serum", variant=None,
        category="Beauty & Personal Care/Skin Care", search_terms=["serum"],
        confidence=0.5, reasoning="blurry", missing_info="the concentration %",
        suggested_photo="the front label, straight-on",
    )
    with patch("app.reply_composer.send_typing", AsyncMock()), \
         patch("app.reply_composer.send_text", AsyncMock()) as mock_send:
        await compose_and_send("chat1", ItemState.NEEDS_ANGLE, result)
    text = mock_send.call_args.args[1]
    assert "front label" in text
    assert "try again" not in text.lower()


async def test_unbuyable_names_the_item():
    result = Identification(
        object_type="laptop", brand="Apple", product="MacBook Pro 14\"",
        variant=None, category="Electronics/Laptops", search_terms=[],
        confidence=0.9, reasoning="clear", missing_info=None, suggested_photo=None,
    )
    with patch("app.reply_composer.send_typing", AsyncMock()), \
         patch("app.reply_composer.send_text", AsyncMock()) as mock_send:
        await compose_and_send("chat1", ItemState.UNBUYABLE, result)
    text = mock_send.call_args.args[1]
    assert "MacBook Pro" in text
    assert "can't buy" in text
