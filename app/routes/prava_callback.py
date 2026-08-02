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
    get_mandate_id_for_session,
    poll_payment_result,
    report_status,
)
from app.routes.webhook import send_text

router = APIRouter()
logger = logging.getLogger(__name__)

_MANDATE_POLL_ATTEMPTS = 30
_MANDATE_POLL_INTERVAL_SECONDS = 3


async def _attach_mandate_from_setup_session(session_id: str) -> bool:
    """Complete an authorize-only mandate_setup session: save mandate_id, no charge."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT p.item_id, i.brand, i.product, p.status, i.mandate_id
               FROM purchases p JOIN items i ON i.id = p.item_id
               WHERE p.prava_session_id = %s""",
            (session_id,),
        )
        row = cur.fetchone()
    if row is None:
        return False
    item_id, brand, product, purchase_status, existing_mandate = row
    if existing_mandate:
        return True
    if purchase_status not in {"MANDATE_SETUP", "AWAITING_APPROVAL"}:
        return False

    mandate_id = await get_mandate_id_for_session(session_id)
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
        await send_text(
            chat_id,
            f"Refills enabled for {brand} {product}. Text “refill …” anytime — no passkey.",
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
) -> None:
    """After first checkout, start authorize-only mandate setup (no Visa cycle burn)."""
    mandate_session = await create_session_with_mandate(
        amount_paise=price_paise,
        merchant=merchant,
        line_items=[{"name": f"{brand} {product}", "price": price_paise / 100}],
        cap_paise=DEFAULT_MANDATE_CAP_PAISE,
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
    await send_text(chat_id, "One more step — approve standing refills (no charge now):")
    await send_text(chat_id, mandate_session.approval_url)
    await _poll_mandate_setup(mandate_session.session_id)


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
        _phone,
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

    # Idempotent: Prava redirect may omit session_id, so we also poll after
    # sending the approval link — both paths can race.
    if purchase_status in {"PAID", "FAILED"} or item_state in {"ORDERED", "FAILED"}:
        return

    result: PaymentResult | None = None
    try:
        # Prava skill: poll until credentials exist — callback often fires early.
        result = await poll_payment_result(session_id)
        if result.status == "failed" or not result.credentials_ready:
            raise ValueError(
                f"Payment result is not ready (status={result.status!r})"
            )
        assert result.txn_ref_id is not None
        await report_status(session_id, "APPROVED", result.txn_ref_id)
        new_state = "PAID"
    except Exception:
        logger.exception("Could not finalize Prava session %s", session_id)
        if result is not None and result.txn_ref_id:
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
        await send_text(chat_id, f"Ordered ✅ · {brand} {product} · saved to your shelf")
        if price_paise and merchant:
            try:
                await _offer_mandate_setup(
                    item_id, chat_id, brand, product, merchant, price_paise
                )
            except Exception:
                logger.exception(
                    "Could not start mandate setup after order for item %s", item_id
                )
    elif chat_id:
        await send_text(chat_id, "Payment didn't go through — the session was declined.")


@router.get("/prava/callback")
async def prava_callback(
    background_tasks: BackgroundTasks,
    session_id: str | None = None,
    session: str | None = None,
):
    # Prava hosted checkout redirects to callback_url as-is (often with no
    # query params). Finalization is primarily driven by post-link polling;
    # if a session id is present we still finalize here.
    sid = session_id or session
    if sid:
        background_tasks.add_task(_finalize_payment, sid)
    return HTMLResponse(
        "<html><body>Payment processed — you can close this tab.</body></html>"
    )
