import httpx

from app.config import settings
from app.routes.webhook import LINQ_BASE, send_text
from app.state_machine import ItemState
from app.vision import Identification


async def send_typing(chat_id: str, on: bool) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{LINQ_BASE}/chats/{chat_id}/typing",
            headers={"Authorization": f"Bearer {settings.linq_api_token}"},
            json={"typing": on},
            timeout=10,
        )


async def compose_and_send(
    chat_id: str, state: ItemState, result: Identification
) -> None:
    await send_typing(chat_id, True)
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
            f"I can't buy that — it's not something I can get through the "
            f"merchants I use."
        )
    elif state == ItemState.IDENTIFIED:
        text = f"Got it — {result.brand} {result.product} ({result.variant}). Looking it up."
    else:
        text = f"({state.value})"
    await send_typing(chat_id, False)
    await send_text(chat_id, text)
