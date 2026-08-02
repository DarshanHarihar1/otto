from app.prava import PaymentResult
from app.prava import charge_mandate as _prava_charge_mandate

DEFAULT_MANDATE_CAP_PAISE = 100_000  # ₹1,000, per spec's demo scenario


class MandateCapExceeded(Exception):
    def __init__(self, requested_paise: int, cap_paise: int):
        self.requested_paise = requested_paise
        self.cap_paise = cap_paise
        super().__init__(f"{requested_paise} exceeds cap {cap_paise}")


async def charge_mandate(
    mandate_id: str,
    amount_paise: int,
    cap_paise: int = DEFAULT_MANDATE_CAP_PAISE,
) -> PaymentResult:
    if amount_paise > cap_paise:
        raise MandateCapExceeded(amount_paise, cap_paise)
    return await _prava_charge_mandate(mandate_id, amount_paise)
