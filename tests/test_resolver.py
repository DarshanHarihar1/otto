from unittest.mock import AsyncMock, patch

from app.luna import VariantMatch
from app.registry import Registry
from app.resolver import resolve
from app.vision import Identification

REGISTRY = Registry(
    _by_category={
        "Beauty & Personal Care/Skin Care": [
            "beminimalist.co",
            "other-skin-store.com",
        ],
    }
)


def _identification() -> Identification:
    return Identification(
        object_type="serum",
        brand="Minimalist",
        product="Salicylic Acid Serum",
        variant="2%, 30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"],
        confidence=0.95,
        reasoning="clear",
        missing_info=None,
        suggested_photo=None,
    )


def _match() -> VariantMatch:
    return VariantMatch(
        best_match_handle="sal-serum",
        similarity=0.9,
        shared_attributes=["salicylic acid"],
        differences=[],
        one_line_pitch="Exact match",
    )


async def test_confident_brand_routes_to_single_store_only():
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                return_value=[
                    {
                        "handle": "sal-serum",
                        "title": "Salicylic Acid 2% Serum",
                        "vendor": "Minimalist India",
                    }
                ]
            ),
        ) as mock_search,
        patch("app.resolver.match_variant", AsyncMock(return_value=_match())),
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 123, "price": "549.00"}]}),
        ),
    ):
        quote = await resolve(_identification(), REGISTRY)

    assert quote is not None
    assert quote.merchant == "beminimalist.co"
    assert quote.price_paise == 54900
    mock_search.assert_awaited_once_with(
        "beminimalist.co", "salicylic acid 2% serum"
    )


async def test_confident_brand_rejects_vendor_that_is_brand_prefix():
    identification = _identification().model_copy(update={"brand": "Aesop"})
    aesop_registry = Registry(
        _by_category={"Beauty & Personal Care/Skin Care": ["aesop.com"]}
    )
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                return_value=[
                    {
                        "handle": "aesop-serum",
                        "title": "Salicylic Acid Serum",
                        "vendor": "A",
                    }
                ]
            ),
        ),
        patch("app.resolver.match_variant", AsyncMock(return_value=_match())) as mock_match,
        patch("app.resolver.get_product", AsyncMock()) as mock_get_product,
    ):
        quote = await resolve(identification, aesop_registry)

    assert quote is None
    mock_match.assert_not_awaited()
    mock_get_product.assert_not_awaited()


async def test_confident_brand_rejects_wrong_vendor_candidate():
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                return_value=[
                    {
                        "handle": "sal-serum",
                        "title": "Salicylic Acid 2% Serum",
                        "vendor": "Wrong Brand",
                    }
                ]
            ),
        ),
        patch("app.resolver.match_variant", AsyncMock(return_value=_match())) as mock_match,
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 123, "price": "549.00"}]}),
        ) as mock_get_product,
    ):
        quote = await resolve(_identification(), REGISTRY)

    assert quote is None
    mock_match.assert_not_awaited()
    mock_get_product.assert_not_awaited()


async def test_price_comes_from_shopify_response_not_model():
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                return_value=[
                    {
                        "handle": "sal-serum",
                        "title": "Salicylic Acid 2% Serum",
                        "vendor": "Minimalist",
                    }
                ]
            ),
        ),
        patch("app.resolver.match_variant", AsyncMock(return_value=_match())),
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 123, "price": "999.00"}]}),
        ) as mock_get_product,
    ):
        quote = await resolve(_identification(), REGISTRY)

    assert quote is not None
    assert quote.price_paise == 99900
    mock_get_product.assert_awaited_once_with("beminimalist.co", "sal-serum")


async def test_fanout_gets_winning_handle_from_its_source_domain():
    identification = _identification().model_copy(update={"confidence": 0.5})
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                side_effect=[
                    [
                        {
                            "handle": "first-serum",
                            "title": "Salicylic Acid Serum",
                            "vendor": "Minimalist",
                        }
                    ],
                    [
                        {
                            "handle": "winning-serum",
                            "title": "Salicylic Acid Serum 2%",
                            "vendor": "Minimalist",
                        }
                    ],
                ]
            ),
        ),
        patch(
            "app.resolver.match_variant",
            AsyncMock(return_value=_match().model_copy(update={"best_match_handle": "winning-serum"})),
        ),
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 456, "price": "599.00"}]}),
        ) as mock_get_product,
    ):
        quote = await resolve(identification, REGISTRY)

    assert quote is not None
    assert quote.merchant == "other-skin-store.com"
    mock_get_product.assert_awaited_once_with("other-skin-store.com", "winning-serum")


async def test_no_quote_when_variant_match_is_missing():
    with (
        patch("app.resolver.search_suggest", AsyncMock(return_value=[{"handle": "sal-serum"}])),
        patch("app.resolver.match_variant", AsyncMock(return_value=None)),
        patch("app.resolver.get_product", AsyncMock()) as mock_get_product,
    ):
        quote = await resolve(_identification(), REGISTRY)

    assert quote is None
    mock_get_product.assert_not_awaited()
