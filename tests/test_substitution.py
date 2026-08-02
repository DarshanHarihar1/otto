from unittest.mock import AsyncMock, patch

from app.luna import VariantMatch
from app.registry import Registry
from app.substitution import (
    NO_SUBSTITUTE_CATEGORIES,
    _substitute_queries,
    find_substitute,
)
from app.vision import Identification

SKINCARE_REGISTRY = Registry(
    _by_category={
        "Beauty & Personal Care/Skin Care": ["beminimalist.co"],
    }
)
HEALTH_REGISTRY = Registry(
    _by_category={
        "Health/Pharmacy": ["kapiva.in"],
    }
)


def test_substitute_queries_prefer_brand_stripped_terms():
    identification = Identification(
        object_type="bottle",
        brand="Vaseline",
        product="Intensive Care Cocoa Glow Serum-in-Lotion",
        variant="Cocoa Butter",
        category="Beauty & Personal Care/Skin Care",
        search_terms=[
            "Vaseline Intensive Care Cocoa Glow Serum-in-Lotion",
            "Vaseline Cocoa Glow cocoa butter 48H body lotion",
        ],
        confidence=0.9,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )
    queries = _substitute_queries(identification)
    assert "Intensive Care Cocoa Glow Serum-in-Lotion" in queries
    assert "Cocoa Glow cocoa butter 48H body lotion" in queries
    assert "body lotion" in queries
    assert not any(q.lower().startswith("vaseline vaseline") for q in queries)


async def test_health_category_never_substitutes_even_with_good_candidates():
    identification = Identification(
        object_type="tablet bottle",
        brand="Dolo",
        product="Paracetamol 650",
        variant="15 tablets",
        category="Health/Pharmacy",
        search_terms=["paracetamol 650"],
        confidence=0.9,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )
    with patch(
        "app.substitution.search_suggest",
        AsyncMock(
            return_value=[{"handle": "paracetamol-650", "title": "Paracetamol 650mg"}]
        ),
    ) as mock_search:
        offer = await find_substitute(identification, HEALTH_REGISTRY)
    assert offer is None
    mock_search.assert_not_called()  # gate must short-circuit before any network call
    assert "Health/Pharmacy" in NO_SUBSTITUTE_CATEGORIES


async def test_skincare_category_offers_labelled_substitute():
    identification = Identification(
        object_type="bottle",
        brand="Dove",
        product="Moisturizer",
        variant="50ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["moisturizer fragrance free"],
        confidence=0.9,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )
    fake_match = VariantMatch(
        best_match_handle="moisturizer-50ml",
        similarity=0.75,
        shared_attributes=["moisturizer", "fragrance-free", "50ml"],
        differences=["different brand", "has niacinamide"],
        one_line_pitch="Same job, different brand, fragrance-free too.",
    )
    with (
        patch(
            "app.substitution.search_suggest",
            AsyncMock(
                return_value=[
                    {"handle": "moisturizer-50ml", "title": "Moisturizer 50ml"}
                ]
            ),
        ),
        patch("app.substitution.match_substitute", AsyncMock(return_value=fake_match)),
        patch(
            "app.substitution.get_product",
            AsyncMock(
                return_value={
                    "title": "Moisturizer 50ml",
                    "vendor": "Minimalist",
                    "variants": [{"id": 1, "price": "399.00"}],
                }
            ),
        ),
    ):
        offer = await find_substitute(identification, SKINCARE_REGISTRY)
    assert offer is not None
    assert offer.price_paise == 39900
    assert offer.shopify_variant_id == "1"
    assert offer.title == "Moisturizer 50ml"
    assert offer.brand == "Minimalist"
    assert "different brand" in offer.match.differences


async def test_weak_similarity_below_floor_offers_nothing():
    identification = Identification(
        object_type="bottle",
        brand="Dove",
        product="Moisturizer",
        variant="50ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["moisturizer"],
        confidence=0.9,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )
    fake_match = VariantMatch(
        best_match_handle="unrelated-item",
        similarity=0.3,
        shared_attributes=[],
        differences=[],
        one_line_pitch="",
    )
    with (
        patch(
            "app.substitution.search_suggest",
            AsyncMock(
                return_value=[{"handle": "unrelated-item", "title": "Something else"}]
            ),
        ),
        patch("app.substitution.match_substitute", AsyncMock(return_value=fake_match)),
    ):
        offer = await find_substitute(identification, SKINCARE_REGISTRY)
    assert offer is None
