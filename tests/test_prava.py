import httpx
import respx

from app.config import settings
from app.prava import create_session, get_payment_result, report_status


@respx.mock
async def test_create_session_returns_real_approval_url():
    route = respx.post(f"{settings.prava_base_url}/v1/sessions").mock(
        return_value=httpx.Response(
            201,
            json={
                "session_id": "session-1",
                "iframe_url": "https://prava.example/approve",
            },
        )
    )
    session = await create_session(
        amount_paise=54900,
        merchant="beminimalist.co",
        line_items=[{"name": "Salicylic Acid Serum", "price": 549.00}],
    )

    assert session.session_id == "session-1"
    assert session.approval_url == "https://prava.example/approve"
    assert route.called


@respx.mock
async def test_report_status_sends_declined_status():
    route = respx.post(
        f"{settings.prava_base_url}/v1/sessions/session-1/report-status"
    ).mock(return_value=httpx.Response(200, json={}))

    await report_status("session-1", "DECLINED", txn_ref_id="txn-1")

    assert route.called
    assert route.calls[0].request.content == b'{"txn_ref_id":"txn-1","txn_status":"DECLINED"}'


@respx.mock
async def test_get_payment_result_exposes_non_ready_status_without_credentials():
    respx.get(
        f"{settings.prava_base_url}/v1/sessions/session-1/payment-result"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "failed",
                "transactions": [{"line_items": [{}]}],
            },
        )
    )

    result = await get_payment_result("session-1")

    assert result.status == "failed"
    assert not result.credentials_ready
