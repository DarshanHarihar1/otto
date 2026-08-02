import asyncio
import re
import time
from decimal import Decimal

import httpx
from pydantic import BaseModel

from app.config import settings

_TIMEOUT_SECONDS = 15
_POLL_INTERVAL_SECONDS = 3
_POLL_ATTEMPTS = 30  # ~90s — Prava SDK skill guidance


class Session(BaseModel):
    session_id: str
    approval_url: str


class PaymentResult(BaseModel):
    status: str
    card_number: str | None = None
    cvv: str | None = None
    expiry: str | None = None
    txn_ref_id: str | None = None

    @property
    def credentials_ready(self) -> bool:
        return bool(self.card_number and self.cvv and self.txn_ref_id)


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
        "user_email": "demo@example.com",
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
            params={"_t": str(int(time.time() * 1000))},
        )
        response.raise_for_status()
        data = response.json()

    line_item = next(
        (
            line_item
            for transaction in data.get("transactions", [])
            for line_item in transaction.get("line_items", [])
        ),
        {},
    )
    expiry_month = line_item.get("expiry_month")
    expiry_year = line_item.get("expiry_year")
    return PaymentResult(
        status=data.get("status", ""),
        card_number=line_item.get("token"),
        cvv=line_item.get("dynamic_cvv"),
        expiry=(
            f"{expiry_month}/{expiry_year}"
            if expiry_month is not None and expiry_year is not None
            else None
        ),
        txn_ref_id=line_item.get("txn_ref_id"),
    )


async def poll_payment_result(session_id: str) -> PaymentResult:
    """Poll until credentials are ready, failed, or attempts exhausted.

    Per Prava SDK skill: poll ~every 3s up to ~90s. Ready when status is
    awaiting_result/completed with token+cvv+txn_ref_id.
    """
    last: PaymentResult | None = None
    for attempt in range(_POLL_ATTEMPTS):
        last = await get_payment_result(session_id)
        if last.status == "failed":
            return last
        if last.status in {"awaiting_result", "completed"} and last.credentials_ready:
            return last
        if attempt + 1 < _POLL_ATTEMPTS:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    if last is None:
        raise TimeoutError(f"No payment result for session {session_id}")
    return last


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
