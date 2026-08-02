from unittest.mock import AsyncMock, patch

import respx
from httpx import Response

from app.reply_composer import compose_and_send, send_typing
from app.routes.webhook import LINQ_BASE
from app.state_machine import ItemState
from app.vision import Identification


async def test_needs_angle_asks_specific_angle():
    result = Identification(
        object_type="bottle", brand="Minimalist", product="Serum", variant=None,
        category="Beauty & Personal Care/Skin Care", search_terms=["serum"],
        confidence=0.5, reasoning="blurry", missing_info="the concentration %",
        suggested_photo="the front label, straight-on",
    )
    with patch("app.reply_composer.send_with_typing", AsyncMock()) as mock_send:
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
    with patch("app.reply_composer.send_with_typing", AsyncMock()) as mock_send:
        await compose_and_send("chat1", ItemState.UNBUYABLE, result)
    text = mock_send.call_args.args[1]
    assert "MacBook Pro" in text
    assert "can't buy" in text
    assert "checkout" in text


async def test_identified_uses_fallbacks_for_missing_product_details():
    result = Identification(
        object_type="bottle", brand=None, product=None, variant=None,
        category="Beauty & Personal Care/Skin Care", search_terms=["serum"],
        confidence=0.95, reasoning="clear", missing_info=None, suggested_photo=None,
    )
    with patch("app.reply_composer.send_with_typing", AsyncMock()) as mock_send:
        await compose_and_send("chat1", ItemState.IDENTIFIED, result)

    assert "None" not in mock_send.call_args.args[1]


@respx.mock
async def test_send_typing_uses_post_to_start_and_delete_to_stop():
    start = respx.post(f"{LINQ_BASE}/chats/chat1/typing").mock(
        return_value=Response(200)
    )
    stop = respx.delete(f"{LINQ_BASE}/chats/chat1/typing").mock(
        return_value=Response(200)
    )

    await send_typing("chat1", True)
    await send_typing("chat1", False)

    assert start.called
    assert stop.called
    assert start.calls[0].request.content == b""
