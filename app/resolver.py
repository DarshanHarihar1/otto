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


async def _search_domain(domain: str, query: str) -> tuple[str, list[dict]]:
    try:
        return domain, await search_suggest(domain, query)
    except Exception:
        return domain, []


async def resolve(identification: Identification, registry: Registry) -> Quote | None:
    """Find a purchase quote from Shopify's current product data."""
    query = " ".join(identification.search_terms) or identification.product or ""

    if identification.brand and identification.confidence >= BRAND_CONFIDENCE_FLOOR:
        brand_domain = _domain_for_brand(identification, registry)
        domains = [brand_domain] if brand_domain else []
    elif identification.category in registry.categories():
        domains = registry.domains_for_category(identification.category)
    else:
        domains = registry.all_domains()

    if not domains:
        return None

    all_candidates: list[tuple[str, dict]] = []
    search_results = await asyncio.gather(
        *(_search_domain(domain, query) for domain in domains)
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
    product = await get_product(domain, match.best_match_handle)
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
        handle=match.best_match_handle,
    )
