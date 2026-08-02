from pydantic import BaseModel

from app.luna import VariantMatch, match_variant
from app.registry import Registry
from app.shopify_client import get_product, search_suggest
from app.vision import Identification

NO_SUBSTITUTE_CATEGORIES = {
    "Health/Pharmacy",
    "Health/Health Conditions & Concerns",
}

SIMILARITY_FLOOR = 0.6


class SubstituteOffer(BaseModel):
    merchant: str
    handle: str
    shopify_variant_id: str
    price_paise: int
    match: VariantMatch


async def find_substitute(
    identification: Identification, registry: Registry
) -> SubstituteOffer | None:
    category = identification.category
    if category is None or category in NO_SUBSTITUTE_CATEGORIES:
        return None
    if category not in registry.categories():
        return None

    query = " ".join(identification.search_terms) or identification.product or ""
    domains = registry.domains_for_category(category)

    all_candidates: list[tuple[str, dict]] = []
    for domain in domains:
        results = await search_suggest(domain, query)
        all_candidates.extend((domain, r) for r in results)

    if not all_candidates:
        return None

    candidates = [c for _, c in all_candidates]
    match = await match_variant(identification, candidates)
    if not match.best_match_handle or match.similarity < SIMILARITY_FLOOR:
        return None

    domain = next(
        d for d, c in all_candidates if c.get("handle") == match.best_match_handle
    )
    product = await get_product(domain, match.best_match_handle)
    variant = product["variants"][0]
    return SubstituteOffer(
        merchant=domain,
        handle=match.best_match_handle,
        shopify_variant_id=str(variant["id"]),
        price_paise=int(float(variant["price"]) * 100),
        match=match,
    )
