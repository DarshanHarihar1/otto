from enum import Enum

from app.config import settings
from app.vision import Identification


class ItemState(str, Enum):
    RECEIVED = "RECEIVED"
    IDENTIFYING = "IDENTIFYING"
    NEEDS_ANGLE = "NEEDS_ANGLE"
    UNBUYABLE = "UNBUYABLE"
    SUBSTITUTE_OFFERED = "SUBSTITUTE_OFFERED"
    IDENTIFIED = "IDENTIFIED"
    DECLINED_SUB = "DECLINED_SUB"
    QUOTED = "QUOTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PAID = "PAID"
    ORDERED = "ORDERED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    ABANDONED = "ABANDONED"


def gate_identification(
    result: Identification, category_registry: set[str]
) -> ItemState:
    if result.confidence < settings.confidence_threshold:
        return ItemState.NEEDS_ANGLE
    if result.category not in category_registry:
        return ItemState.UNBUYABLE
    return ItemState.IDENTIFIED
