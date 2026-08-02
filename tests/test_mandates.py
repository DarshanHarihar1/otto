import httpx
import respx

from app.config import settings
from app.prava import (
    charge_mandate,
    create_session_with_mandate,
    get_mandate_id_for_customer,
    get_mandate_id_for_session,
)


@respx.mock
async def test_create_session_with_mandate_posts_checkout_intent_and_cap():
    route = respx.post(f"{settings.prava_base_url}/v1/sessions").mock(
        return_value=httpx.Response(
            201,
            json={
                "session_id": "ses_mandate_1",
                "iframe_url": "https://sandbox.collect.prava.space/approve",
            },
        )
    )

    session = await create_session_with_mandate(
        amount_paise=59900,
        merchant="bombayshavingcompany.com",
        line_items=[{"name": "Power Play NXT Trimmer", "price": 599.00}],
        cap_paise=100_000,
    )

    assert session.session_id == "ses_mandate_1"
    assert session.approval_url.startswith("http")
    assert route.called
    body = route.calls[0].request.read()
    import json

    payload = json.loads(body)
    assert payload["mandate_setup"]["intent"] == "mandate_setup"
    assert payload["mandate_setup"]["merchant_scope"] == "listed"
    assert payload["total_amount"] == "1000.00"  # max(599, 1000) authorized cap
    assert payload["currency"] == "INR"
    assert payload["user_email"] == "demo@example.com"
    assert session.authorize_only is True


@respx.mock
async def test_get_mandate_id_for_session_lists_standing_mandates():
    respx.get(f"{settings.prava_base_url}/v1/mandates").mock(
        return_value=httpx.Response(
            200,
            json={
                "mandates": [
                    {
                        "id": "mdt_one_time",
                        "status": "active",
                        "recurringFrequency": "one_time",
                        "createdAt": "2026-08-02T04:00:00Z",
                    },
                    {
                        "id": "mdt_old",
                        "status": "active",
                        "recurringFrequency": "monthly",
                        "createdAt": "2026-07-01T00:00:00Z",
                    },
                    {
                        "id": "mdt_new",
                        "status": "active",
                        "recurringFrequency": "monthly",
                        "createdAt": "2026-08-02T00:00:00Z",
                    },
                ]
            },
        )
    )

    mandate_id = await get_mandate_id_for_session("ses_anything")
    assert mandate_id == "mdt_new"


@respx.mock
async def test_get_mandate_id_for_customer_respects_created_after():
    respx.get(f"{settings.prava_base_url}/v1/mandates").mock(
        return_value=httpx.Response(
            200,
            json={
                "mandates": [
                    {
                        "id": "mdt_old",
                        "status": "active",
                        "recurringFrequency": "monthly",
                        "createdAt": "2026-07-01T00:00:00Z",
                    },
                    {
                        "id": "mdt_new",
                        "status": "active",
                        "recurringFrequency": "monthly",
                        "createdAt": "2026-08-02T12:00:00Z",
                    },
                ]
            },
        )
    )

    assert (
        await get_mandate_id_for_customer(created_after_iso="2026-08-02T11:00:00Z")
        == "mdt_new"
    )
    assert await get_mandate_id_for_customer(created_after_iso="2026-08-02T13:00:00Z") is None


@respx.mock
async def test_charge_mandate_maps_credentials():
    respx.post(f"{settings.prava_base_url}/v1/mandates/mdt_123/charge").mock(
        return_value=httpx.Response(
            200,
            json={
                "mandateId": "mdt_123",
                "transactionId": "txn_9",
                "status": "awaiting_result",
                "credentials": {
                    "token": "4111111111111111",
                    "dynamicCvv": "123",
                    "expiryMonth": "12",
                    "expiryYear": "2030",
                },
            },
        )
    )

    result = await charge_mandate("mdt_123", 59900)
    assert result.credentials_ready
    assert result.card_number == "4111111111111111"
    assert result.cvv == "123"
    assert result.expiry == "12/2030"
    assert result.txn_ref_id == "txn_9"
