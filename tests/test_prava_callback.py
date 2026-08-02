from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app
from app.prava import PaymentResult

client = TestClient(app)


def test_callback_acks_immediately():
    resp = client.get("/prava/callback", params={"session_id": "nonexistent"})

    assert resp.status_code == 200
    assert "Payment processed" in resp.text


def test_callback_acks_without_session_id():
    # Prava hosted mode redirects to callback_url with no query params.
    resp = client.get("/prava/callback")

    assert resp.status_code == 200
    assert "Payment processed" in resp.text


async def test_finalize_payment_marks_ordered_and_notifies_saved_chat():
    from app.routes.prava_callback import _finalize_payment

    mock_cur = MagicMock()
    mock_cur.fetchone.side_effect = [
        (
            "item-uuid-1",
            "AWAITING_APPROVAL",
            "+919900475117",
            "Minimalist",
            "Serum",
            "AWAITING_APPROVAL",
        ),
        ("chat-uuid-1",),
    ]
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__.return_value = mock_cur
    mock_cursor_cm.__exit__.return_value = False
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor_cm
    mock_conn_cm = MagicMock()
    mock_conn_cm.__enter__.return_value = mock_conn
    mock_conn_cm.__exit__.return_value = False

    with (
        patch(
            "app.routes.prava_callback.get_conn",
            return_value=mock_conn_cm,
        ),
        patch(
            "app.routes.prava_callback.poll_payment_result",
            AsyncMock(
                return_value=PaymentResult(
                    status="awaiting_result",
                    card_number="4111111111111111",
                    cvv="123",
                    expiry="01/2030",
                    txn_ref_id="txn-1",
                )
            ),
        ),
        patch("app.routes.prava_callback.report_status", AsyncMock()),
        patch("app.routes.prava_callback.send_text", AsyncMock()) as mock_send,
    ):
        await _finalize_payment("prava-session-1")

    calls = mock_cur.execute.call_args_list
    assert "UPDATE items SET state = %s" in calls[1][0][0]
    assert calls[1][0][1] == ("PAID", "item-uuid-1")
    assert "UPDATE purchases SET status = %s" in calls[2][0][0]
    assert "state = 'ORDERED'" in calls[3][0][0]
    assert "kind = 'chat_ref'" in calls[4][0][0]
    mock_send.assert_awaited_once_with(
        "chat-uuid-1", "Ordered ✅ · Minimalist Serum · saved to your shelf"
    )


async def test_finalize_payment_fails_without_approving_non_ready_result():
    from app.routes.prava_callback import _finalize_payment

    mock_cur = MagicMock()
    mock_cur.fetchone.side_effect = [
        (
            "item-uuid-1",
            "AWAITING_APPROVAL",
            "+919900475117",
            "Minimalist",
            "Serum",
            "AWAITING_APPROVAL",
        ),
        ("chat-uuid-1",),
    ]
    mock_cursor_cm = MagicMock()
    mock_cursor_cm.__enter__.return_value = mock_cur
    mock_cursor_cm.__exit__.return_value = False
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor_cm
    mock_conn_cm = MagicMock()
    mock_conn_cm.__enter__.return_value = mock_conn
    mock_conn_cm.__exit__.return_value = False

    with (
        patch(
            "app.routes.prava_callback.get_conn",
            return_value=mock_conn_cm,
        ),
        patch(
            "app.routes.prava_callback.poll_payment_result",
            AsyncMock(
                return_value=PaymentResult(
                    status="failed",
                    card_number=None,
                    cvv=None,
                    expiry=None,
                    txn_ref_id=None,
                )
            ),
        ),
        patch("app.routes.prava_callback.report_status", AsyncMock()) as mock_report,
        patch("app.routes.prava_callback.send_text", AsyncMock()) as mock_send,
    ):
        await _finalize_payment("prava-session-1")

    calls = mock_cur.execute.call_args_list
    assert calls[1][0][1] == ("FAILED", "item-uuid-1")
    assert not any("state = 'ORDERED'" in call[0][0] for call in calls)
    mock_report.assert_not_awaited()
    mock_send.assert_awaited_once_with(
        "chat-uuid-1", "Payment didn't go through — the session was declined."
    )
