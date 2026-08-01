import asyncio
import json
import logging
import uuid

from app.db import get_conn
from app.registry import load_registry
from app.media import archive_photo, download_media
from app.reply_composer import compose_and_send, send_typing
from app.resolver import resolve
from app.routes.webhook import send_text
from app.state_machine import ItemState, gate_identification
from app.vision import identify

logger = logging.getLogger(__name__)

_REGISTRY = load_registry()


async def handle_photo_message(
    user_phone: str, chat_id: str, media_url: str
) -> str | None:
    try:
        try:
            await send_typing(chat_id, True)
        except Exception:
            logger.exception("Could not start typing indicator for chat %s", chat_id)

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE phone = %s", (user_phone,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"No user found for phone {user_phone!r}")
            user_id = row[0]
            cur.execute(
                """SELECT id FROM items
                   WHERE user_id = %s AND state = 'NEEDS_ANGLE'
                     AND updated_at > now() - interval '10 minutes'
                   ORDER BY updated_at DESC LIMIT 1""",
                (user_id,),
            )
            open_item = cur.fetchone()
            if open_item:
                item_id = open_item[0]
            else:
                item_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO items (id, user_id, state) VALUES (%s, %s, 'IDENTIFYING')",
                    (item_id, user_id),
                )

        image_bytes = await download_media(media_url)
        storage_path = await asyncio.to_thread(archive_photo, item_id, image_bytes)
        result = await identify(image_bytes, _REGISTRY.categories())
        if result is None:
            raise ValueError("Vision identification returned no parsed result")
        state = gate_identification(result, _REGISTRY.categories())
        logger.info(
            "Identification gate chose state=%s confidence=%s category=%r",
            state.value,
            result.confidence,
            result.category,
        )

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE items
                SET brand = %s, product = %s, variant = %s, category = %s,
                    confidence = %s, photo_storage_path = %s, state = %s,
                    updated_at = now()
                WHERE id = %s
                """,
                (
                    result.brand,
                    result.product,
                    result.variant,
                    result.category,
                    result.confidence,
                    storage_path,
                    state.value,
                    item_id,
                ),
            )
            cur.execute(
                "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'identified', %s)",
                (item_id, json.dumps(result.model_dump())),
            )

        try:
            await send_typing(chat_id, False)
        except Exception:
            logger.exception("Could not stop typing indicator for chat %s", chat_id)
        await compose_and_send(chat_id, state, result)
        if state == ItemState.IDENTIFIED:
            quote = await resolve(result, _REGISTRY)
            if quote is None:
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE items SET state = 'UNBUYABLE', updated_at = now() WHERE id = %s",
                        (item_id,),
                    )
                await send_text(
                    chat_id,
                    f"Couldn't find {result.brand} {result.product} anywhere I can buy from.",
                )
            else:
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """UPDATE items SET state = 'QUOTED', merchant = %s,
                           shopify_variant_id = %s, last_price_paise = %s, updated_at = now()
                           WHERE id = %s""",
                        (
                            quote.merchant,
                            quote.shopify_variant_id,
                            quote.price_paise,
                            item_id,
                        ),
                    )
                price_rupees = quote.price_paise / 100
                await send_text(
                    chat_id,
                    f"{result.brand} {result.product} · {result.variant} · ₹{price_rupees:.0f}",
                )
        return item_id
    except Exception:
        logger.exception("Photo message pipeline failed")
        try:
            await send_text(
                chat_id, "Couldn't read that one — try a photo of the front label?"
            )
        except Exception:
            logger.error("Could not send photo-pipeline failure response")
        return None
