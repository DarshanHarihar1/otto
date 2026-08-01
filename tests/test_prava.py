import httpx
import pytest

from app.prava import create_session, report_status


async def test_create_session_returns_real_approval_url():
    session = await create_session(
        amount_paise=54900,
        merchant="beminimalist.co",
        line_items=[{"name": "Salicylic Acid Serum", "price": 549.00}],
    )

    assert session.session_id
    assert session.approval_url.startswith("http")


async def test_report_status_sends_declined_status_to_sandbox():
    session = await create_session(
        amount_paise=10000,
        merchant="test-merchant",
        line_items=[{"name": "test item", "price": 100.00}],
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await report_status(
            session.session_id, "DECLINED", txn_ref_id="sandbox-test-transaction"
        )

    assert exc_info.value.response.status_code == 400
    assert exc_info.value.response.json()["error"]["code"] == "INVALID_STATE"
