import asyncio
from decimal import Decimal

from pydantic import BaseModel

from app.luna import match_shopify_variant, match_variant
from app.registry import Registry
from app.shopify_client import get_product, search_suggest
from app.vision import Identification

BRAND_CONFIDENCE_FLOOR = 0.85
SINGLE_STORE_SIMILARITY_FLOOR = 0.6
"""Minimum similarity when a confident brand limits search to one store."""

BREADTH_SIMILARITY_FLOOR = 0.75
"""Minimum similarity for category or global multi-store fan-out."""

MIN_SAVINGS_PAISE = 1000
"""Ignore alts that save less than ₹10 — avoids noise offers."""

_SHOP_AROUND_DOMAIN_LIMIT = 2

# Multi-brand retailers are the only realistic same-brand price alts in this
# D2C-heavy registry — prefer them over alphabetical brand stores.
_MULTI_BRAND_STORES = frozenset(
    {
        "clinikally.com",
        "innovist.com",
    }
)


class Quote(BaseModel):
    merchant: str
    shopify_variant_id: str
    price_paise: int
    handle: str


def _domain_for_brand(identification: Identification, registry: Registry) -> str | None:
    brand_slug = (identification.brand or "").lower().replace(" ", "")
    for domain in registry.all_domains():
        if brand_slug and brand_slug in domain.lower():
            return domain
    return None


def _brand_matches(vendor: str, brand: str) -> bool:
    vendor = vendor.casefold().strip()
    brand = brand.casefold().strip()
    return bool(vendor and brand) and brand in vendor


def _search_queries(identification: Identification) -> list[str]:
    """Prefer individual vision search terms over concatenating them.

    Shopify suggest often returns nothing for a glued multi-term query.
    """
    queries: list[str] = []
    for term in identification.search_terms or []:
        term = (term or "").strip()
        if term and term not in queries:
            queries.append(term)
    product = (identification.product or "").strip()
    if product and product not in queries:
        queries.append(product)
    brand = (identification.brand or "").strip()
    if brand and product:
        branded = f"{brand} {product}"
        if branded not in queries:
            queries.append(branded)
    return queries


async def _search_domain(domain: str, queries: list[str]) -> tuple[str, list[dict]]:
    """Merge suggest hits across all queries (dedupe by handle)."""
    seen: set[str] = set()
    merged: list[dict] = []
    for query in queries:
        try:
            results = await search_suggest(domain, query)
        except Exception:
            results = []
        for candidate in results:
            handle = candidate.get("handle")
            if not handle or handle in seen:
                continue
            seen.add(handle)
            merged.append(candidate)
    return domain, merged


def _shop_around_domains(
    identification: Identification,
    registry: Registry,
    primary_merchant: str,
    *,
    limit: int = _SHOP_AROUND_DOMAIN_LIMIT,
) -> list[str]:
    category = identification.category
    if not category or category not in registry.categories():
        return []
    brand_slug = (identification.brand or "").lower().replace(" ", "")
    others = [
        domain
        for domain in registry.domains_for_category(category)
        if domain != primary_merchant
    ]

    def brand_owned(domain: str) -> bool:
        return bool(brand_slug and brand_slug in domain.lower())

    def rank(domain: str) -> tuple[int, int, str]:
        multi = 0 if domain in _MULTI_BRAND_STORES else 1
        owned = 1 if brand_owned(domain) else 0
        return (multi, owned, domain)

    others.sort(key=rank)
    return others[:limit]


async def _quote_for_handle(
    domain: str,
    handle: str,
    identification: Identification,
) -> Quote | None:
    product = await get_product(domain, handle)
    variants = product.get("variants", [])
    if not variants:
        return None
    if len(variants) == 1:
        variant = variants[0]
    else:
        variant_match = await match_shopify_variant(identification, variants)
        if variant_match.shopify_variant_id is None:
            return None
        variants_by_id = {str(variant["id"]): variant for variant in variants}
        variant = variants_by_id.get(variant_match.shopify_variant_id)
        if variant is None:
            return None
    return Quote(
        merchant=domain,
        shopify_variant_id=str(variant["id"]),
        price_paise=int(Decimal(str(variant["price"])) * 100),
        handle=handle,
    )


async def resolve(identification: Identification, registry: Registry) -> Quote | None:
    """Find a purchase quote from Shopify's current product data."""
    queries = _search_queries(identification)
    if not queries:
        return None

    if identification.brand and identification.confidence >= BRAND_CONFIDENCE_FLOOR:
        brand_domain = _domain_for_brand(identification, registry)
        if brand_domain:
            domains = [brand_domain]
        elif identification.category in registry.categories():
            # Brand is confident but has no D2C slug in the registry (e.g. Chemist
            # at Play on innovist.com) — fan out with the brand vendor filter.
            domains = registry.domains_for_category(identification.category)
        else:
            domains = []
    elif identification.category in registry.categories():
        domains = registry.domains_for_category(identification.category)
    else:
        domains = registry.all_domains()

    if not domains:
        return None

    all_candidates: list[tuple[str, dict]] = []
    search_results = await asyncio.gather(
        *(_search_domain(domain, queries) for domain in domains)
    )
    for domain, results in search_results:
        all_candidates.extend((domain, result) for result in results)

    if not all_candidates:
        return None

    exact = [
        (domain, candidate)
        for domain, candidate in all_candidates
        if identification.brand
        and _brand_matches(candidate.get("vendor", ""), identification.brand)
    ]
    if not exact:
        return None

    candidates = [candidate for _, candidate in exact]

    similarity_floor = (
        SINGLE_STORE_SIMILARITY_FLOOR if len(domains) == 1 else BREADTH_SIMILARITY_FLOOR
    )
    match = await match_variant(identification, candidates)
    if (
        match is None
        or not match.best_match_handle
        or match.similarity < similarity_floor
    ):
        return None

    source = next(
        (
            (domain, candidate)
            for domain, candidate in exact
            if candidate.get("handle") == match.best_match_handle
        ),
        None,
    )
    if source is None:
        return None

    domain, _ = source
    return await _quote_for_handle(domain, match.best_match_handle, identification)


async def shop_around(
    identification: Identification,
    registry: Registry,
    primary: Quote,
) -> Quote | None:
    """Find the same brand product cheaper at up to 2 other category stores."""
    if not identification.brand:
        return None
    queries = _search_queries(identification)
    if not queries:
        return None

    domains = _shop_around_domains(identification, registry, primary.merchant)
    if not domains:
        return None

    search_results = await asyncio.gather(
        *(_search_domain(domain, queries) for domain in domains)
    )
    alt_quotes: list[Quote] = []
    for domain, results in search_results:
        exact = [
            candidate
            for candidate in results
            if _brand_matches(candidate.get("vendor", ""), identification.brand)
        ]
        if not exact:
            continue
        match = await match_variant(identification, exact)
        if (
            match is None
            or not match.best_match_handle
            or match.similarity < BREADTH_SIMILARITY_FLOOR
        ):
            continue
        if not any(c.get("handle") == match.best_match_handle for c in exact):
            continue
        quote = await _quote_for_handle(
            domain, match.best_match_handle, identification
        )
        if quote is not None:
            alt_quotes.append(quote)

    if not alt_quotes:
        return None
    best = min(alt_quotes, key=lambda quote: quote.price_paise)
    if best.price_paise + MIN_SAVINGS_PAISE < primary.price_paise:
        return best
    return None


def store_short_name(merchant: str) -> str:
    host = merchant.removeprefix("https://").removeprefix("http://").split("/")[0]
    parts = host.split(".")
    if len(parts) >= 2 and parts[-1] in {"com", "in", "co", "net", "org", "shop"}:
        if parts[-2] == "myshopify" and len(parts) >= 3:
            return parts[-3]
        if parts[-1] == "co" and len(parts) >= 2:
            return parts[0]
        return parts[0]
    return parts[0] if parts else merchant
