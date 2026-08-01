import asyncio
import json
import logging
import uuid

from app.db import get_conn
from app.media import archive_photo, download_media
from app.routes.webhook import send_text
from app.vision import identify

logger = logging.getLogger(__name__)


async def handle_photo_message(
    user_phone: str, chat_id: str, media_url: str
) -> str | None:
    item_id = str(uuid.uuid4())
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE phone = %s", (user_phone,))
            row = cur.fetchone()
            if row is None:
                raise ValueError(f"No user found for phone {user_phone!r}")
            user_id = row[0]
            cur.execute(
                "INSERT INTO items (id, user_id, state) VALUES (%s, %s, 'IDENTIFYING')",
                (item_id, user_id),
            )

        image_bytes = await download_media(media_url)
        storage_path = await asyncio.to_thread(archive_photo, item_id, image_bytes)
        result = await identify(image_bytes)
        if result is None:
            raise ValueError("Vision identification returned no parsed result")

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE items
                SET brand = %s, product = %s, variant = %s, category = %s,
                    confidence = %s, photo_storage_path = %s, updated_at = now()
                WHERE id = %s
                """,
                (
                    result.brand,
                    result.product,
                    result.variant,
                    result.category,
                    result.confidence,
                    storage_path,
                    item_id,
                ),
            )
            cur.execute(
                "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'identified', %s)",
                (item_id, json.dumps(result.model_dump())),
            )

        await send_text(
            chat_id,
            f"Got it — {result.brand or 'unknown brand'} {result.product or ''}. "
            f"({result.reasoning})",
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
