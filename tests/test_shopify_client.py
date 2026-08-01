from app.shopify_client import search_suggest, get_product


async def test_search_suggest_against_real_store():
    results = await search_suggest("beminimalist.co", "salicylic acid")
    assert isinstance(results, list)


async def test_get_product_returns_real_variants_with_prices():
    results = await search_suggest("beminimalist.co", "salicylic acid")
    assert results, "expected at least one real product result"
    handle = results[0]["handle"]
    product = await get_product("beminimalist.co", handle)
    assert product["variants"]
    assert "price" in product["variants"][0]
