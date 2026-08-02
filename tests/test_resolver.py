from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

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
    assert {call.args[0] for call in mock_search.await_args_list} == {
        "beminimalist.co"
    }
    assert mock_search.await_args_list[0].args == (
        "beminimalist.co",
        "salicylic acid 2% serum",
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


async def test_quote_uses_luna_selected_shopify_variant_and_price():
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
            "app.resolver.match_shopify_variant",
            AsyncMock(return_value=SimpleNamespace(shopify_variant_id="456")),
            create=True,
        ) as mock_match_shopify_variant,
        patch(
            "app.resolver.get_product",
            AsyncMock(
                return_value={
                    "variants": [
                        {"id": 123, "title": "10ml", "price": "199.00"},
                        {"id": 456, "title": "30ml", "price": "549.00"},
                    ]
                }
            ),
        ),
    ):
        quote = await resolve(_identification(), REGISTRY)

    assert quote is not None
    assert quote.shopify_variant_id == "456"
    assert quote.price_paise == 54900
    mock_match_shopify_variant.assert_awaited_once()


async def test_fanout_gets_winning_handle_from_its_source_domain():
    identification = _identification().model_copy(update={"confidence": 0.5})

    async def search(domain: str, query: str) -> list[dict]:
        if domain == "beminimalist.co":
            return [
                {
                    "handle": "first-serum",
                    "title": "Salicylic Acid Serum",
                    "vendor": "Minimalist",
                }
            ]
        return [
            {
                "handle": "winning-serum",
                "title": "Salicylic Acid Serum 2%",
                "vendor": "Minimalist",
            }
        ]

    with (
        patch("app.resolver.search_suggest", side_effect=search),
        patch(
            "app.resolver.match_variant",
            AsyncMock(
                return_value=_match().model_copy(
                    update={"best_match_handle": "winning-serum"}
                )
            ),
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


async def test_breadth_requires_higher_similarity_than_confident_single_store():
    weak_match = _match().model_copy(update={"similarity": 0.65})
    search_result = [
        {
            "handle": "sal-serum",
            "title": "Salicylic Acid 2% Serum",
            "vendor": "Minimalist",
        }
    ]
    with (
        patch("app.resolver.search_suggest", AsyncMock(return_value=search_result)),
        patch("app.resolver.match_variant", AsyncMock(return_value=weak_match)),
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 123, "price": "549.00"}]}),
        ) as mock_get_product,
    ):
        single_store_quote = await resolve(_identification(), REGISTRY)
        breadth_quote = await resolve(
            _identification().model_copy(update={"confidence": 0.5}), REGISTRY
        )

    assert single_store_quote is not None
    assert breadth_quote is None
    assert mock_get_product.await_count == 1


async def test_fanout_keeps_searching_when_one_domain_fails():
    identification = _identification().model_copy(update={"confidence": 0.5})

    async def search(domain: str, query: str) -> list[dict]:
        if domain == "beminimalist.co":
            raise TimeoutError("merchant timed out")
        return [
            {
                "handle": "winning-serum",
                "title": "Salicylic Acid Serum 2%",
                "vendor": "Minimalist",
            }
        ]

    with (
        patch("app.resolver.search_suggest", side_effect=search),
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


async def test_tries_search_terms_individually_instead_of_concatenating():
    identification = _identification().model_copy(
        update={
            "search_terms": [
                "salicylic acid 2% serum",
                "extra noise term that would break a joined query",
            ]
        }
    )

    async def search(domain: str, query: str) -> list[dict]:
        if query == "salicylic acid 2% serum":
            return [
                {
                    "handle": "sal-serum",
                    "title": "Salicylic Acid 2% Serum",
                    "vendor": "Minimalist",
                }
            ]
        return []

    with (
        patch("app.resolver.search_suggest", side_effect=search) as mock_search,
        patch("app.resolver.match_variant", AsyncMock(return_value=_match())),
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 123, "price": "549.00"}]}),
        ),
    ):
        quote = await resolve(identification, REGISTRY)

    assert quote is not None
    assert quote.price_paise == 54900
    assert mock_search.await_args_list[0].args == (
        "beminimalist.co",
        "salicylic acid 2% serum",
    )
    assert all(
        "extra noise" not in call.args[1] or call.args[1] == "extra noise term that would break a joined query"
        for call in mock_search.await_args_list
    )
    # Must not search the concatenated blob of both terms.
    assert not any(
        "salicylic acid 2% serum extra noise" in call.args[1]
        for call in mock_search.await_args_list
    )


async def test_shop_around_returns_cheaper_same_brand_alt():
    from app.resolver import Quote, shop_around

    primary = Quote(
        merchant="beminimalist.co",
        shopify_variant_id="1",
        price_paise=59900,
        handle="sal-serum",
    )
    registry = Registry(
        _by_category={
            "Beauty & Personal Care/Skin Care": [
                "beminimalist.co",
                "clinikally.com",
                "mamaearth.in",
            ],
        }
    )
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                return_value=[
                    {
                        "handle": "sal-serum-alt",
                        "title": "Salicylic Acid 2% Serum",
                        "vendor": "Minimalist",
                    }
                ]
            ),
        ) as mock_search,
        patch(
            "app.resolver.match_variant",
            AsyncMock(
                return_value=VariantMatch(
                    best_match_handle="sal-serum-alt",
                    similarity=0.9,
                    shared_attributes=[],
                    differences=[],
                    one_line_pitch="match",
                )
            ),
        ),
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 99, "price": "549.00"}]}),
        ),
    ):
        alt = await shop_around(_identification(), registry, primary)

    assert alt is not None
    assert alt.merchant == "clinikally.com"
    assert alt.price_paise == 54900
    # Prefer non-brand-slug stores; mamaearth may or may not be searched second.
    assert mock_search.await_args_list[0].args[0] == "clinikally.com"


async def test_shop_around_returns_none_when_alt_not_cheaper_enough():
    from app.resolver import Quote, shop_around

    primary = Quote(
        merchant="beminimalist.co",
        shopify_variant_id="1",
        price_paise=59900,
        handle="sal-serum",
    )
    registry = Registry(
        _by_category={
            "Beauty & Personal Care/Skin Care": [
                "beminimalist.co",
                "clinikally.com",
            ],
        }
    )
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                return_value=[
                    {
                        "handle": "sal-serum-alt",
                        "title": "Salicylic Acid 2% Serum",
                        "vendor": "Minimalist",
                    }
                ]
            ),
        ),
        patch(
            "app.resolver.match_variant",
            AsyncMock(
                return_value=VariantMatch(
                    best_match_handle="sal-serum-alt",
                    similarity=0.9,
                    shared_attributes=[],
                    differences=[],
                    one_line_pitch="match",
                )
            ),
        ),
        patch(
            "app.resolver.get_product",
            AsyncMock(return_value={"variants": [{"id": 99, "price": "595.00"}]}),
        ),
    ):
        alt = await shop_around(_identification(), registry, primary)

    assert alt is None


async def test_shop_around_rejects_other_brand_vendor():
    from app.resolver import Quote, shop_around

    primary = Quote(
        merchant="beminimalist.co",
        shopify_variant_id="1",
        price_paise=59900,
        handle="sal-serum",
    )
    registry = Registry(
        _by_category={
            "Beauty & Personal Care/Skin Care": [
                "beminimalist.co",
                "clinikally.com",
            ],
        }
    )
    with (
        patch(
            "app.resolver.search_suggest",
            AsyncMock(
                return_value=[
                    {
                        "handle": "other-serum",
                        "title": "Salicylic Acid Serum",
                        "vendor": "Dot & Key",
                    }
                ]
            ),
        ),
        patch("app.resolver.match_variant", AsyncMock()) as mock_match,
        patch("app.resolver.get_product", AsyncMock()) as mock_get,
    ):
        alt = await shop_around(_identification(), registry, primary)

    assert alt is None
    mock_match.assert_not_awaited()
    mock_get.assert_not_awaited()


async def test_search_domain_merges_hits_across_queries():
    """First query often returns a multipack; later queries find the single."""
    from app.resolver import _search_domain

    async def search(domain: str, query: str) -> list[dict]:
        if "236 ml" in query:
            return [
                {
                    "handle": "wash-pack-of-3",
                    "title": "Body Wash Pack of 3",
                    "vendor": "Chemist at Play",
                }
            ]
        if query == "Brightening Body Wash":
            return [
                {
                    "handle": "brightening-body-wash",
                    "title": "Brightening Body Wash",
                    "vendor": "Chemist at Play",
                }
            ]
        return []

    with patch("app.resolver.search_suggest", side_effect=search) as mock_search:
        domain, results = await _search_domain(
            "innovist.com",
            [
                "Chemist at Play Brightening Body Wash 3% 236 ml",
                "Brightening Body Wash",
            ],
        )

    assert domain == "innovist.com"
    assert [r["handle"] for r in results] == [
        "wash-pack-of-3",
        "brightening-body-wash",
    ]
    assert mock_search.await_count == 2
