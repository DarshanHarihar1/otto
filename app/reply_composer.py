import logging

import httpx

from app.config import settings
from app.routes.webhook import LINQ_BASE
from app.routes.webhook import send_text as _raw_send_text
from app.state_machine import ItemState
from app.vision import Identification

logger = logging.getLogger(__name__)


async def send_typing(chat_id: str, on: bool = True) -> None:
    try:
        async with httpx.AsyncClient() as client:
            request = client.post if on else client.delete
            response = await request(
                f"{LINQ_BASE}/chats/{chat_id}/typing",
                headers={"Authorization": f"Bearer {settings.linq_api_token}"},
                timeout=10,
            )
            response.raise_for_status()
    except Exception:
        logger.exception("Could not update typing indicator for chat %s", chat_id)


async def send_with_typing(chat_id: str, text: str) -> None:
    await send_typing(chat_id, True)
    try:
        await _raw_send_text(chat_id, text)
    finally:
        await send_typing(chat_id, False)


async def compose_and_send(
    chat_id: str, state: ItemState, result: Identification
) -> None:
    if state == ItemState.NEEDS_ANGLE:
        angle = result.suggested_photo or "the front label, straight-on"
        text = (
            f"Fairly sure that's {result.brand or 'this'} "
            f"{result.product or 'product'}, but I can't read "
            f"{result.missing_info or 'a detail'}. Photo of {angle}?"
        )
    elif state == ItemState.UNBUYABLE:
        text = (
            f"That's {result.brand or ''} {result.product or 'this item'}. "
            f"I can't buy that — it doesn't sell through the checkout I use."
        )
    elif state == ItemState.IDENTIFIED:
        text = (
            f"Got it — {result.brand or 'this'} {result.product or 'item'} "
            f"({result.variant or 'details unavailable'}). Looking it up."
        )
    else:
        text = f"({state.value})"
    await send_with_typing(chat_id, text)
