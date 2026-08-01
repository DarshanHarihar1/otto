import re
from decimal import Decimal

import httpx
from pydantic import BaseModel

from app.config import settings

_TIMEOUT_SECONDS = 15


class Session(BaseModel):
    session_id: str
    approval_url: str


class PaymentResult(BaseModel):
    card_number: str
    cvv: str
    expiry: str
    txn_ref_id: str


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.prava_api_key}"}


def _merchant_url(merchant: str) -> str:
    normalized = merchant.removeprefix("https://").removeprefix("http://").rstrip("/")
    if "." in normalized and " " not in normalized:
        return f"https://{normalized}"

    slug = re.sub(r"[^a-z0-9]+", "-", merchant.lower()).strip("-") or "merchant"
    return f"https://{slug}.example.com"


def _product_details(line_items: list[dict]) -> list[dict]:
    return [
        {
            "description": item["name"],
            "unit_price": f"{Decimal(str(item['price'])):.2f}",
            "quantity": item.get("quantity", 1),
        }
        for item in line_items
    ]


async def create_session(
    amount_paise: int, merchant: str, line_items: list[dict]
) -> Session:
    amount = f"{Decimal(amount_paise) / Decimal(100):.2f}"
    callback_url = f"{settings.public_base_url.rstrip('/')}/prava/callback"
    payload = {
        "user_id": f"otto_{settings.demo_user_phone.lstrip('+')}",
        "user_email": "demo@otto.local",
        "total_amount": amount,
        "currency": "INR",
        "integration_type": "full_checkout",
        "callback_url": callback_url,
        "purchase_context": [
            {
                "merchant_details": {
                    "name": merchant,
                    "url": _merchant_url(merchant),
                    "country_code_iso2": "IN",
                },
                "product_details": _product_details(line_items),
            }
        ],
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.prava_base_url}/v1/sessions",
            headers=_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    return Session(session_id=data["session_id"], approval_url=data["iframe_url"])


async def get_payment_result(session_id: str) -> PaymentResult:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{settings.prava_base_url}/v1/sessions/{session_id}/payment-result",
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()

    line_item = data["transactions"][0]["line_items"][0]
    return PaymentResult(
        card_number=line_item["token"],
        cvv=line_item["dynamic_cvv"],
        expiry=f"{line_item['expiry_month']}/{line_item['expiry_year']}",
        txn_ref_id=line_item["txn_ref_id"],
    )


async def report_status(session_id: str, status: str, txn_ref_id: str) -> None:
    if status not in {"APPROVED", "DECLINED"}:
        raise ValueError("status must be APPROVED or DECLINED")

    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.prava_base_url}/v1/sessions/{session_id}/report-status",
            headers=_headers(),
            json={"txn_ref_id": txn_ref_id, "txn_status": status},
        )
        response.raise_for_status()
