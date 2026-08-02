import asyncio
import json
import logging
import re
import uuid

from app.categories import _registry_path
from app.db import get_conn
from app.registry import load_registry
from app.media import archive_photo, download_media
from app.mandates import MandateCapExceeded, charge_mandate
from app.prava import create_session
from app.reply_composer import compose_and_send, send_typing
from app.reply_composer import send_with_typing as send_text
from app.resolver import resolve, shop_around, store_short_name
from app.shelf import find_shelf_item
from app.state_machine import ItemState, gate_identification
from app.substitution import find_substitute
from app.vision import identify

logger = logging.getLogger(__name__)

_REGISTRY = load_registry(_registry_path())


def _ensure_user(user_phone: str) -> str:
    """Create the user on first contact — Linq can deliver any sender to our bot."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO users (phone)
               VALUES (%s)
               ON CONFLICT (phone) DO UPDATE SET phone = EXCLUDED.phone
               RETURNING id""",
            (user_phone,),
        )
        return str(cur.fetchone()[0])


async def handle_photo_message(
    user_phone: str, chat_id: str, media_url: str
) -> str | None:
    try:
        try:
            await send_typing(chat_id, True)
        except Exception:
            logger.exception("Could not start typing indicator for chat %s", chat_id)

        user_id = _ensure_user(user_phone)
        with get_conn() as conn, conn.cursor() as cur:
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
            if quote is not None:
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
                variant_bit = f", {result.variant}" if result.variant else ""
                await send_text(
                    chat_id,
                    f"found it — {result.brand} {result.product}{variant_bit}. "
                    f"₹{price_rupees:.0f}. want it?",
                )
                try:
                    alt = await shop_around(result, _REGISTRY, quote)
                except Exception:
                    logger.exception(
                        "Shop-around failed for item %s; keeping primary quote",
                        item_id,
                    )
                    alt = None
                if alt is not None:
                    with get_conn() as conn, conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO events (item_id, kind, payload)
                               VALUES (%s, 'cheaper_alt', %s)""",
                            (item_id, json.dumps(alt.model_dump())),
                        )
                    delta_rupees = (quote.price_paise - alt.price_paise) / 100
                    await send_text(
                        chat_id,
                        f"same thing's ₹{delta_rupees:.0f} cheaper at "
                        f"{store_short_name(alt.merchant)} — say switch?",
                    )
            else:
                offer = await find_substitute(result, _REGISTRY)
                if offer is None:
                    with get_conn() as conn, conn.cursor() as cur:
                        cur.execute(
                            "UPDATE items SET state = 'UNBUYABLE', updated_at = now() WHERE id = %s",
                            (item_id,),
                        )
                    await send_text(
                        chat_id,
                        f"none of my stores have {result.brand} {result.product} rn, sorry",
                    )
                else:
                    with get_conn() as conn, conn.cursor() as cur:
                        cur.execute(
                            """UPDATE items SET state = 'SUBSTITUTE_OFFERED',
                               brand = %s, product = %s, merchant = %s,
                               shopify_variant_id = %s, last_price_paise = %s, updated_at = now()
                               WHERE id = %s""",
                            (
                                offer.brand,
                                offer.title,
                                offer.merchant,
                                offer.shopify_variant_id,
                                offer.price_paise,
                                item_id,
                            ),
                        )
                    diffs = "; ".join(offer.match.differences[:2]) or "different brand"
                    await send_text(
                        chat_id,
                        f"can't get the {result.brand} one from my stores. "
                        f"closest: {offer.brand} {offer.title} for ₹{offer.price_paise / 100:.0f}. "
                        f"heads up — {diffs}. want that instead?",
                    )
        return item_id
    except Exception:
        logger.exception("Photo message pipeline failed")
        try:
            await send_text(chat_id, "couldn't read that — front label photo?")
        except Exception:
            logger.error("Could not send photo-pipeline failure response")
        return None


_SWITCH_PHRASES = frozenset({"switch", "cheaper", "buy cheaper", "switch to cheaper"})


def _wants_cheaper_switch(text_lower: str) -> bool:
    """Accept exact phrases and casual replies like 'yeah lets switch.'"""
    cleaned = re.sub(r"[^\w\s]", " ", text_lower)
    cleaned = " ".join(cleaned.split())
    if cleaned in _SWITCH_PHRASES:
        return True
    if "switch" in cleaned.split():
        return True
    if cleaned in {"buy cheaper", "get cheaper"}:
        return True
    return False


async def _begin_checkout(user_phone: str, chat_id: str) -> None:
    """Claim latest QUOTED item and open a Prava approval session."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """WITH quoted_item AS (
                   SELECT i.id
                   FROM items i JOIN users u ON u.id = i.user_id
                   WHERE u.phone = %s AND i.state = 'QUOTED'
                   ORDER BY i.updated_at DESC LIMIT 1
               )
               UPDATE items i
               SET state = 'AWAITING_APPROVAL', updated_at = now()
               FROM quoted_item q
               WHERE i.id = q.id AND i.state = 'QUOTED'
               RETURNING i.id, i.merchant, i.shopify_variant_id,
                         i.last_price_paise, i.brand, i.product""",
            (user_phone,),
        )
        row = cur.fetchone()

    if row is None:
        return

    item_id, merchant, variant_id, price_paise, brand, product = row
    try:
        session = await create_session(
            amount_paise=price_paise,
            merchant=merchant,
            line_items=[
                {
                    "name": f"{brand} {product}",
                    "shopify_variant_id": variant_id,
                    "price": price_paise / 100,
                }
            ],
            user_phone=user_phone,
        )
    except Exception:
        logger.exception("Could not create Prava session for item %s", item_id)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE items SET state = 'QUOTED', updated_at = now()
                   WHERE id = %s AND state = 'AWAITING_APPROVAL'""",
                (item_id,),
            )
        await send_text(chat_id, "checkout hiccup — say yes again?")
        return

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO purchases (item_id, prava_session_id, amount_paise, status) VALUES (%s, %s, %s, 'AWAITING_APPROVAL')",
            (item_id, session.session_id, price_paise),
        )
        cur.execute(
            "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'chat_ref', %s)",
            (item_id, json.dumps({"chat_id": chat_id})),
        )

    await send_text(
        chat_id,
        f"cool, ₹{price_paise / 100:.0f}. approve here (one tap):",
    )
    await send_text(chat_id, session.approval_url)

    # Poll in the background so a slow passkey isn't racing a 90s wait that can
    # mark FAILED before the user finishes. Callback (with or without
    # session_id) can also complete the same session.
    from app.routes.prava_callback import _finalize_payment

    asyncio.create_task(_finalize_payment(session.session_id))


async def _switch_to_cheaper_alt(user_phone: str, chat_id: str) -> bool:
    """Apply latest cheaper_alt event onto the QUOTED item. Returns True if applied."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.id, e.payload
               FROM items i
               JOIN users u ON u.id = i.user_id
               JOIN events e ON e.item_id = i.id AND e.kind = 'cheaper_alt'
               WHERE u.phone = %s AND i.state = 'QUOTED'
               ORDER BY e.id DESC
               LIMIT 1""",
            (user_phone,),
        )
        row = cur.fetchone()
        if row is None:
            return False
        item_id, payload = row
        if isinstance(payload, str):
            payload = json.loads(payload)
        cur.execute(
            """UPDATE items
               SET merchant = %s, shopify_variant_id = %s, last_price_paise = %s,
                   updated_at = now()
               WHERE id = %s AND state = 'QUOTED'""",
            (
                payload["merchant"],
                payload["shopify_variant_id"],
                payload["price_paise"],
                item_id,
            ),
        )
        cur.execute(
            "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'cheaper_alt_accepted', %s)",
            (item_id, json.dumps(payload)),
        )
    return True


async def handle_text_message(user_phone: str, chat_id: str, text: str) -> None:
    text_lower = text.strip().lower()
    _ensure_user(user_phone)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.id FROM items i JOIN users u ON u.id = i.user_id
               WHERE u.phone = %s AND i.state = 'SUBSTITUTE_OFFERED'
               ORDER BY i.updated_at DESC LIMIT 1""",
            (user_phone,),
        )
        sub_row = cur.fetchone()

    if sub_row is not None and text_lower in ("yes", "no"):
        item_id = sub_row[0]
        new_state = "IDENTIFIED" if text_lower == "yes" else "DECLINED_SUB"
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET state = %s, updated_at = now() WHERE id = %s",
                (new_state, item_id),
            )
            cur.execute(
                "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'substitute_response', %s)",
                (item_id, json.dumps({"accepted": text_lower == "yes"})),
            )
        if text_lower == "no":
            await send_text(chat_id, "all good, skipping")
            return
        # "yes": merchant/variant/price already set from SUBSTITUTE_OFFERED; go to QUOTED
        # without re-running resolve() (that could pick a different item).
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE items SET state = 'QUOTED', updated_at = now()
                   WHERE id = %s RETURNING brand, product, merchant, last_price_paise""",
                (item_id,),
            )
            brand, product, _merchant, price_paise = cur.fetchone()
        await send_text(
            chat_id,
            f"locked in — {brand} {product}, ₹{price_paise / 100:.0f}. say yes if you wanna buy",
        )
        return

    if text_lower.startswith("refill "):
        rest = text_lower.removeprefix("refill ").strip()
        qty_match = re.search(r"[x×]\s*(\d+)$", rest)
        quantity = int(qty_match.group(1)) if qty_match else 1
        label_query = re.sub(r"[x×]\s*\d+$", "", rest).strip()

        shelf_item = find_shelf_item(user_phone, label_query)
        if shelf_item is None:
            await send_text(
                chat_id, f"don't have anything saved as “{label_query}” yet"
            )
            return
        if shelf_item.mandate_id is None:
            await send_text(
                chat_id,
                "no refill approval on that one yet — need you to approve once",
            )
            return

        total_paise = shelf_item.last_price_paise * quantity
        try:
            result = await charge_mandate(shelf_item.mandate_id, total_paise)
            if result.status == "failed" or not result.credentials_ready:
                msg = result.error_message or result.status
                raise ValueError(msg)
        except MandateCapExceeded as e:
            await send_text(
                chat_id,
                f"that's ₹{e.requested_paise / 100:,.0f} — over your "
                f"₹{e.cap_paise / 100:,.0f} cap. card network said no. wanna bump the cap?",
            )
            return
        except Exception as exc:
            logger.exception(
                "Mandate charge failed for item %s mandate %s",
                shelf_item.item_id,
                shelf_item.mandate_id,
            )
            detail = str(exc)
            if "payment cycle" in detail.lower() or "already made" in detail.lower():
                await send_text(
                    chat_id,
                    "visa only lets one charge/month on this. next refill opens next cycle",
                )
            else:
                await send_text(chat_id, "refill failed, try again in a bit?")
            return
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO purchases (item_id, amount_paise, status) VALUES (%s, %s, 'PAID')",
                (shelf_item.item_id, total_paise),
            )
        await send_text(
            chat_id,
            f"on it — ₹{total_paise / 100:.0f}, same as last time",
        )
        return

    if _wants_cheaper_switch(text_lower):
        switched = await _switch_to_cheaper_alt(user_phone, chat_id)
        if not switched:
            return
        await _begin_checkout(user_phone, chat_id)
        return

    yes_cleaned = re.sub(r"[^\w\s]", " ", text_lower)
    yes_cleaned = " ".join(yes_cleaned.split())
    if yes_cleaned not in ("yes", "yeah", "yep", "buy", "buy it", "confirm", "want it"):
        return

    await _begin_checkout(user_phone, chat_id)