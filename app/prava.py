import asyncio
import re
import time
from decimal import Decimal

import httpx
from pydantic import BaseModel

from app.config import public_base_url, settings

_TIMEOUT_SECONDS = 15
_POLL_INTERVAL_SECONDS = 3
_POLL_ATTEMPTS = 30  # ~90s — Prava SDK skill guidance


class Session(BaseModel):
    session_id: str
    approval_url: str
    authorize_only: bool = False


class PaymentResult(BaseModel):
    status: str
    card_number: str | None = None
    cvv: str | None = None
    expiry: str | None = None
    txn_ref_id: str | None = None
    error_message: str | None = None

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


def _customer_id(user_phone: str | None = None) -> str:
    phone = (user_phone or settings.demo_user_phone).lstrip("+")
    return f"otto_{phone}"


def _product_details(line_items: list[dict]) -> list[dict]:
    return [
        {
            "description": item["name"],
            "unit_price": f"{Decimal(str(item['price'])):.2f}",
            "quantity": item.get("quantity", 1),
        }
        for item in line_items
    ]


def _session_payload(
    amount_paise: int,
    merchant: str,
    line_items: list[dict],
    *,
    mandate_setup: dict | None = None,
    authorized_amount_paise: int | None = None,
    user_phone: str | None = None,
) -> dict:
    total_paise = (
        amount_paise if authorized_amount_paise is None else authorized_amount_paise
    )
    payload: dict = {
        "user_id": _customer_id(user_phone),
        "user_email": "demo@example.com",
        "total_amount": f"{Decimal(total_paise) / Decimal(100):.2f}",
        "currency": "INR",
        "integration_type": "full_checkout",
        "callback_url": f"{public_base_url().rstrip('/')}/prava/callback",
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
    if mandate_setup is not None:
        payload["mandate_setup"] = mandate_setup
    return payload


async def create_session(
    amount_paise: int,
    merchant: str,
    line_items: list[dict],
    *,
    user_phone: str | None = None,
) -> Session:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.prava_base_url}/v1/sessions",
            headers=_headers(),
            json=_session_payload(
                amount_paise, merchant, line_items, user_phone=user_phone
            ),
        )
        response.raise_for_status()
        data = response.json()
    return Session(session_id=data["session_id"], approval_url=data["iframe_url"])


async def create_session_with_mandate(
    amount_paise: int,
    merchant: str,
    line_items: list[dict],
    cap_paise: int,
    *,
    user_phone: str | None = None,
) -> Session:
    """Set up a standing mandate (docs: intent=mandate_setup, authorize-only).

    Does not mint checkout credentials — first spend is via charge_mandate after
    passkey approval. Using intent=checkout burns Visa's one-charge-per-cycle
    on the first purchase and blocks same-cycle refills.
    """
    mandate_setup = {
        "intent": "mandate_setup",
        "recurring_frequency": "monthly",
        "merchant_scope": "listed",
        "max_charges": 12,
    }
    payload = _session_payload(
        amount_paise,
        merchant,
        line_items,
        mandate_setup=mandate_setup,
        authorized_amount_paise=max(amount_paise, cap_paise),
        user_phone=user_phone,
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.prava_base_url}/v1/sessions",
            headers=_headers(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    return Session(
        session_id=data["session_id"],
        approval_url=data["iframe_url"],
        authorize_only=bool(data.get("authorizeOnly", True)),
    )


async def get_mandate_id_for_customer(
    *,
    created_after_iso: str | None = None,
    user_phone: str | None = None,
) -> str | None:
    """Newest active standing (non-one_time) mandate for the Otto customer.

    Optionally require createdAt >= created_after_iso so a mandate_setup session
    does not accidentally bind an older burned mandate.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{settings.prava_base_url}/v1/mandates",
            headers=_headers(),
            params={"customer_id": _customer_id(user_phone)},
        )
        response.raise_for_status()
        data = response.json()
    mandates = data.get("mandates") or []
    standing = [
        m
        for m in mandates
        if m.get("status") == "active"
        and m.get("recurringFrequency") not in {None, "one_time"}
    ]
    if created_after_iso:
        standing = [
            m
            for m in standing
            if (m.get("createdAt") or "") >= created_after_iso
        ]
    if not standing:
        return None
    standing.sort(
        key=lambda m: m.get("createdAt") or m.get("updatedAt") or "", reverse=True
    )
    mandate_id = standing[0].get("id")
    return mandate_id if isinstance(mandate_id, str) else None


async def get_mandate_id_for_session(session_id: str) -> str | None:
    """Resolve standing mandate id after a mandate checkout/setup session."""
    del session_id  # list is customer-scoped; callers that need freshness use created_after
    return await get_mandate_id_for_customer()


async def charge_mandate(mandate_id: str, amount_paise: int) -> PaymentResult:
    amount = f"{Decimal(amount_paise) / Decimal(100):.2f}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"{settings.prava_base_url}/v1/mandates/{mandate_id}/charge",
            headers=_headers(),
            json={"amount": amount},
        )
        response.raise_for_status()
        data = response.json()

    credentials = data.get("credentials") or {}
    expiry_month = credentials.get("expiryMonth")
    expiry_year = credentials.get("expiryYear")
    status = data.get("status") or ""
    return PaymentResult(
        status=status,
        card_number=credentials.get("token"),
        cvv=credentials.get("dynamicCvv"),
        expiry=(
            f"{expiry_month}/{expiry_year}"
            if expiry_month is not None and expiry_year is not None
            else None
        ),
        txn_ref_id=data.get("transactionId"),
        error_message=data.get("errorMessage") or data.get("errorCode"),
    )


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
    awaiting_result/completed with token+cvv+txn_ref_id. Transient HTTP
    errors are retried — they must not abort the wait for passkey approval.
    """
    last: PaymentResult | None = None
    for attempt in range(_POLL_ATTEMPTS):
        try:
            last = await get_payment_result(session_id)
        except httpx.HTTPError:
            if attempt + 1 < _POLL_ATTEMPTS:
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue
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
