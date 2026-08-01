import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import HTMLResponse

from app.db import get_conn
from app.prava import PaymentResult, poll_payment_result, report_status
from app.routes.webhook import send_text

router = APIRouter()
logger = logging.getLogger(__name__)


async def _finalize_payment(session_id: str) -> None:
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT p.item_id, i.state, u.phone, i.brand, i.product
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
    item_id, _state, _phone, brand, product = row

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
                "UPDATE items SET state = 'ORDERED', updated_at = now() WHERE id = %s",
                (item_id,),
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
    elif chat_id:
        await send_text(chat_id, "Payment didn't go through — the session was declined.")


@router.get("/prava/callback")
async def prava_callback(session_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(_finalize_payment, session_id)
    return HTMLResponse(
        "<html><body>Payment processed — you can close this tab.</body></html>"
    )
