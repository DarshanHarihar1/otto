from app.state_machine import ItemState, gate_identification
from app.vision import Identification

REGISTRY = {"Beauty & Personal Care/Skin Care", "Health/Pharmacy"}


def _result(confidence, category):
    return Identification(
        object_type="bottle",
        brand="Minimalist",
        product="Serum",
        variant="30ml",
        category=category,
        search_terms=["serum"],
        confidence=confidence,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )


def test_low_confidence_triggers_needs_angle():
    state = gate_identification(
        _result(0.5, "Beauty & Personal Care/Skin Care"), REGISTRY
    )
    assert state == ItemState.NEEDS_ANGLE


def test_unknown_category_triggers_unbuyable():
    state = gate_identification(_result(0.95, "Electronics/Laptops"), REGISTRY)
    assert state == ItemState.UNBUYABLE


def test_missing_category_triggers_needs_angle():
    state = gate_identification(_result(0.95, None), REGISTRY)
    assert state == ItemState.NEEDS_ANGLE


def test_confident_known_category_is_identified():
    state = gate_identification(
        _result(0.95, "Beauty & Personal Care/Skin Care"), REGISTRY
    )
    assert state == ItemState.IDENTIFIED
