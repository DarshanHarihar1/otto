import json
import uuid

from app.db import get_conn
from app.media import archive_photo, download_media
from app.routes.webhook import send_text
from app.vision import identify


async def handle_photo_message(user_phone: str, chat_id: str, media_url: str) -> str:
    item_id = str(uuid.uuid4())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE phone = %s", (user_phone,))
        row = cur.fetchone()
        user_id = row[0]
        cur.execute(
            "INSERT INTO items (id, user_id, state) VALUES (%s, %s, 'IDENTIFYING')",
            (item_id, user_id),
        )

    image_bytes = await download_media(media_url)
    storage_path = archive_photo(item_id, image_bytes)
    result = await identify(image_bytes)

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
