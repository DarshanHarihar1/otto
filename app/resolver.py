from decimal import Decimal

from pydantic import BaseModel

from app.luna import match_variant
from app.registry import Registry
from app.shopify_client import get_product, search_suggest
from app.vision import Identification

BRAND_CONFIDENCE_FLOOR = 0.85


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
    for domain in domains:
        results = await search_suggest(domain, query)
        all_candidates.extend((domain, result) for result in results)

    if not all_candidates:
        return None

    if identification.brand and identification.confidence >= BRAND_CONFIDENCE_FLOOR:
        candidates = [candidate for _, candidate in all_candidates]
        domain = all_candidates[0][0]
    else:
        exact = [
            (domain, candidate)
            for domain, candidate in all_candidates
            if identification.brand
            and identification.brand.lower() in candidate.get("vendor", "").lower()
        ]
        if not exact:
            return None
        candidates = [candidate for _, candidate in exact]
        domain = exact[0][0]

    match = await match_variant(identification, candidates)
    if match is None or not match.best_match_handle or match.similarity < 0.6:
        return None

    product = await get_product(domain, match.best_match_handle)
    variant = product["variants"][0]
    return Quote(
        merchant=domain,
        shopify_variant_id=str(variant["id"]),
        price_paise=int(Decimal(str(variant["price"])) * 100),
        handle=match.best_match_handle,
    )
