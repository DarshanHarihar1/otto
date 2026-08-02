import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import HTMLResponse

from app.db import get_conn
from app.mandates import DEFAULT_MANDATE_CAP_PAISE
from app.prava import (
    PaymentResult,
    create_session_with_mandate,
    get_mandate_id_for_customer,
    poll_payment_result,
    report_status,
)
from app.reply_composer import send_with_typing as send_text

router = APIRouter()
logger = logging.getLogger(__name__)

_MANDATE_POLL_ATTEMPTS = 60
_MANDATE_POLL_INTERVAL_SECONDS = 3


async def _attach_mandate_from_setup_session(session_id: str) -> bool:
    """Complete an authorize-only mandate_setup session: save mandate_id, no charge."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT p.item_id, i.brand, i.product, p.status, i.mandate_id, p.created_at,
                      u.phone
               FROM purchases p
               JOIN items i ON i.id = p.item_id
               JOIN users u ON u.id = i.user_id
               WHERE p.prava_session_id = %s""",
            (session_id,),
        )
        row = cur.fetchone()
    if row is None:
        return False
    item_id, brand, product, purchase_status, existing_mandate, created_at, phone = row
    if existing_mandate and purchase_status == "MANDATE_READY":
        return True
    if purchase_status not in {"MANDATE_SETUP", "AWAITING_APPROVAL"}:
        return False

    created_after = created_at.isoformat() if created_at is not None else None
    mandate_id = await get_mandate_id_for_customer(
        created_after_iso=created_after, user_phone=phone
    )
    if not mandate_id:
        return False

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE items SET mandate_id = %s WHERE id = %s",
            (mandate_id, item_id),
        )
        cur.execute(
            "UPDATE purchases SET status = 'MANDATE_READY' WHERE prava_session_id = %s",
            (session_id,),
        )
        cur.execute(
            "SELECT payload->>'chat_id' FROM events WHERE item_id = %s AND kind = 'chat_ref' ORDER BY id DESC LIMIT 1",
            (item_id,),
        )
        chat_row = cur.fetchone()
    chat_id = chat_row[0] if chat_row else None
    if chat_id:
        short = (product or brand or "it").split()[0].lower()
        await send_text(
            chat_id,
            f"refills on. just text “refill {short}” next time, no passkey",
        )
    return True


async def _poll_mandate_setup(session_id: str) -> None:
    for attempt in range(_MANDATE_POLL_ATTEMPTS):
        try:
            if await _attach_mandate_from_setup_session(session_id):
                return
        except Exception:
            logger.exception("Mandate setup poll failed for %s", session_id)
            return
        if attempt + 1 < _MANDATE_POLL_ATTEMPTS:
            await asyncio.sleep(_MANDATE_POLL_INTERVAL_SECONDS)


async def _offer_mandate_setup(
    item_id: str,
    chat_id: str,
    brand: str,
    product: str,
    merchant: str,
    price_paise: int,
    *,
    user_phone: str | None = None,
) -> None:
    """After first checkout, start authorize-only mandate setup (no Visa cycle burn)."""
    mandate_session = await create_session_with_mandate(
        amount_paise=price_paise,
        merchant=merchant,
        line_items=[{"name": f"{brand} {product}", "price": price_paise / 100}],
        cap_paise=DEFAULT_MANDATE_CAP_PAISE,
        user_phone=user_phone,
    )
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO purchases (item_id, prava_session_id, amount_paise, status)
               VALUES (%s, %s, %s, 'MANDATE_SETUP')""",
            (item_id, mandate_session.session_id, price_paise),
        )
        cur.execute(
            "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'chat_ref', %s)",
            (item_id, json.dumps({"chat_id": chat_id})),
        )
    await send_text(chat_id, "one more thing — turn on easy refills (won't charge now):")
    await send_text(chat_id, mandate_session.approval_url)
    await _poll_mandate_setup(mandate_session.session_id)


def _latest_open_checkout_session_id() -> str | None:
    """Prava hosted redirect often hits callback_url with no session_id."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT prava_session_id FROM purchases
               WHERE prava_session_id IS NOT NULL
                 AND status IN ('AWAITING_APPROVAL', 'FAILED')
                 AND created_at > now() - interval '2 hours'
               ORDER BY created_at DESC
               LIMIT 1"""
        )
        row = cur.fetchone()
    return row[0] if row else None


async def _finalize_payment(session_id: str) -> None:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT p.item_id, i.state, u.phone, i.brand, i.product, p.status,
                          i.last_price_paise, i.merchant
                   FROM purchases p JOIN items i ON i.id = p.item_id
                   JOIN users u ON u.id = i.user_id
                   WHERE p.prava_session_id = %s""",
                (session_id,),
            )
            row = cur.fetchone()
    except Exception:
        logger.exception("Could not load payment for Prava session %s", session_id)
        return
    if row is None:
        return
    (
        item_id,
        item_state,
        phone,
        brand,
        product,
        purchase_status,
        price_paise,
        merchant,
    ) = row

    if purchase_status in {"MANDATE_SETUP", "MANDATE_READY"}:
        try:
            await _attach_mandate_from_setup_session(session_id)
        except Exception:
            logger.exception("Mandate setup attach failed for %s", session_id)
        return

    # Idempotent success. FAILED is recoverable: poll can race ahead of passkey
    # and mark bounce while Prava later returns credentials on the same session.
    if purchase_status == "PAID" or item_state == "ORDERED":
        return

    result: PaymentResult | None = None
    try:
        # Prava skill: poll until credentials exist — callback often fires early.
        result = await poll_payment_result(session_id)
        if result.status == "failed":
            raise ValueError("payment result status=failed")
        if not result.credentials_ready:
            # Still waiting on passkey — don't mark FAILED (sandbox can also
            # time out / 500 while the user hasn't opened the link yet).
            logger.info(
                "Payment still pending for session %s (status=%r)",
                session_id,
                result.status,
            )
            return
        assert result.txn_ref_id is not None
        await report_status(session_id, "APPROVED", result.txn_ref_id)
        new_state = "PAID"
    except Exception:
        logger.exception("Could not finalize Prava session %s", session_id)
        # Network / timeout while waiting for approval: leave AWAITING_APPROVAL
        # so the user can still complete the passkey (callback may finish it).
        # Never downgrade an already-FAILED row again.
        if result is None or result.status != "failed":
            return
        if purchase_status == "FAILED" or item_state == "FAILED":
            return
        if result.txn_ref_id:
            try:
                await report_status(session_id, "DECLINED", result.txn_ref_id)
            except Exception:
                pass
        new_state = "FAILED"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE items SET state = %s, updated_at = now() WHERE id = %s",
            (new_state, item_id),
        )
        cur.execute(
            "UPDATE purchases SET status = %s WHERE prava_session_id = %s",
            (new_state, session_id),
        )

    if new_state == "PAID":
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET state = 'ORDERED', label = %s, updated_at = now() WHERE id = %s",
                (f"{brand} {product}", item_id),
            )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload->>'chat_id' FROM events WHERE item_id = %s AND kind = 'chat_ref' ORDER BY id DESC LIMIT 1",
            (item_id,),
        )
        chat_row = cur.fetchone()
    chat_id = chat_row[0] if chat_row else None

    if chat_id and new_state == "PAID":
        await send_text(chat_id, f"ordered ✅ {product}. saved on your shelf")
        if price_paise and merchant:
            try:
                await _offer_mandate_setup(
                    item_id,
                    chat_id,
                    brand,
                    product,
                    merchant,
                    price_paise,
                    user_phone=phone,
                )
            except Exception:
                logger.exception(
                    "Could not start mandate setup after order for item %s", item_id
                )
    elif chat_id:
        await send_text(chat_id, "payment bounced. try again?")


@router.get("/prava/callback")
async def prava_callback(
    background_tasks: BackgroundTasks,
    session_id: str | None = None,
    session: str | None = None,
):
    # Prava hosted checkout redirects to callback_url as-is (often with no
    # query params). Fall back to the latest open checkout session so a
    # successful passkey still finalizes after a premature bounce.
    sid = session_id or session or _latest_open_checkout_session_id()
    if sid:
        background_tasks.add_task(_finalize_payment, sid)
    return HTMLResponse(
        "<html><body>Payment processed — you can close this tab.</body></html>"
    )
