from app.prava import PaymentResult
from app.prava import charge_mandate as _prava_charge_mandate

DEFAULT_MANDATE_CAP_PAISE = 100_000  # ₹1,000, per spec's demo scenario


async def charge_mandate(mandate_id: str, amount_paise: int) -> PaymentResult:
    return await _prava_charge_mandate(mandate_id, amount_paise)
