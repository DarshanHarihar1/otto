import asyncio
import re

from pydantic import BaseModel

from app.luna import VariantMatch, match_substitute
from app.registry import Registry
from app.shopify_client import get_product, search_suggest
from app.vision import Identification

NO_SUBSTITUTE_CATEGORIES = {
    "Health/Pharmacy",
    "Health/Health Conditions & Concerns",
}

SIMILARITY_FLOOR = 0.6

_GENERIC_FALLBACKS = (
    "body lotion",
    "moisturizer",
    "serum",
    "sunscreen",
    "face wash",
    "shampoo",
    "conditioner",
    "body wash",
)


class SubstituteOffer(BaseModel):
    merchant: str
    handle: str
    shopify_variant_id: str
    price_paise: int
    title: str
    brand: str
    match: VariantMatch


def _strip_brand(text: str, brand: str) -> str:
    if not brand:
        return text.strip()
    return re.sub(re.escape(brand), "", text, flags=re.IGNORECASE).strip(" -–,/")


def _substitute_queries(identification: Identification) -> list[str]:
    """Cross-brand search queries — prefer product job over the missing brand.

    Concatenating vision search_terms (as the original plan snippet did) returns
    nothing from Shopify suggest; brand-heavy queries also miss other merchants.
    """
    brand = (identification.brand or "").strip()
    queries: list[str] = []

    def add(q: str) -> None:
        q = " ".join(q.split()).strip(" -–,/")
        if q and q not in queries:
            queries.append(q)

    product = (identification.product or "").strip()
    if product:
        add(_strip_brand(product, brand))
        add(product)

    for term in identification.search_terms or []:
        term = (term or "").strip()
        if not term:
            continue
        stripped = _strip_brand(term, brand)
        add(stripped)
        # Keep original only if it isn't just the brand name.
        if stripped and stripped.casefold() != term.casefold():
            pass  # brand-stripped preferred; skip brand-locked original
        elif not brand:
            add(term)

    variant = (identification.variant or "").strip()
    if variant:
        add(_strip_brand(variant, brand))

    blob = " ".join(
        [
            product,
            variant or "",
            *(identification.search_terms or []),
        ]
    ).casefold()
    for fallback in _GENERIC_FALLBACKS:
        token = fallback.split()[-1]
        if fallback in blob or token in blob:
            add(fallback)

    return queries


async def _search_domain(domain: str, queries: list[str]) -> tuple[str, list[dict]]:
    for query in queries:
        try:
            results = await search_suggest(domain, query)
        except Exception:
            results = []
        if results:
            return domain, results
    return domain, []


async def find_substitute(
    identification: Identification, registry: Registry
) -> SubstituteOffer | None:
    category = identification.category
    if category is None or category in NO_SUBSTITUTE_CATEGORIES:
        return None
    if category not in registry.categories():
        return None

    queries = _substitute_queries(identification)
    if not queries:
        return None

    domains = registry.domains_for_category(category)
    all_candidates: list[tuple[str, dict]] = []
    search_results = await asyncio.gather(
        *(_search_domain(domain, queries) for domain in domains)
    )
    for domain, results in search_results:
        all_candidates.extend((domain, result) for result in results)

    if not all_candidates:
        return None

    candidates = [c for _, c in all_candidates]
    # Cap for the LLM: prefer titles that share product tokens with the target.
    tokens = {
        t
        for t in re.findall(
            r"[a-z0-9]+",
            " ".join(
                [
                    identification.product or "",
                    identification.variant or "",
                    *(identification.search_terms or []),
                ]
            ).casefold(),
        )
        if len(t) > 3 and t not in {(identification.brand or "").casefold()}
    }

    def _overlap(c: dict) -> int:
        title = (c.get("title") or "").casefold()
        return sum(1 for t in tokens if t in title)

    candidates.sort(key=_overlap, reverse=True)
    candidates = candidates[:40]
    match = await match_substitute(identification, candidates)
    if not match.best_match_handle or match.similarity < SIMILARITY_FLOOR:
        return None

    domain = next(
        d for d, c in all_candidates if c.get("handle") == match.best_match_handle
    )
    product = await get_product(domain, match.best_match_handle)
    variant = product["variants"][0]
    title = (product.get("title") or match.best_match_handle).strip()
    brand = (product.get("vendor") or domain).strip()
    return SubstituteOffer(
        merchant=domain,
        handle=match.best_match_handle,
        shopify_variant_id=str(variant["id"]),
        price_paise=int(float(variant["price"]) * 100),
        title=title,
        brand=brand,
        match=match,
    )
