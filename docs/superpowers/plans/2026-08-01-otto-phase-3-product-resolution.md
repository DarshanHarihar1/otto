# Otto Phase 3: Product Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From an `IDENTIFIED` item, find the exact merchant + variant + real ₹ price using the verified registry, following the spec's brand-hard-filter routing flowchart, and reach `QUOTED`.

**Architecture:** A registry loader over `data/merchants.csv`, a Shopify public-JSON client, and a resolver implementing the exact routing logic from `otto-architecture.md` §3: confident brand → single-store call; otherwise fan out by category or globally, raising the confidence bar as breadth increases.

**Tech Stack:** `httpx.AsyncClient` for concurrent fan-out, `gpt-5.6-luna` for variant matching.

## Global Constraints

*(Same as Phase 0. Highlights for this phase — these are the two rules most likely to break the demo if skipped:)*

- **Brand hard filter is absolute:** if vision returns a confident brand, only accept results from that brand's own store. Fan-out only fires when brand is unknown. A confidently-wrong-brand purchase undoes the whole thesis.
- **Price always comes from the live Shopify JSON response, never from the model.** `luna` picks *which* variant; the merchant's own JSON says what it costs.

**Prerequisite:** Phase 2 complete and its gate passed (state machine live, `IDENTIFIED` reachable).

---

## File Structure

```
app/
├── registry.py       # loads data/merchants.csv -> category/domain index
├── shopify_client.py  # GET /search/suggest.json, GET /products/{handle}.json
├── luna.py             # gpt-5.6-luna structured variant-matching call
├── resolver.py          # brand-filter routing per spec flowchart
└── orchestrator.py       # (modified) IDENTIFIED -> resolve -> QUOTED
tests/
├── test_registry.py
├── test_shopify_client.py
├── test_luna.py
└── test_resolver.py
```

---

### Task 1: Registry loader

**Files:**
- Create: `app/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `data/merchants.csv` (Phase 0 Task 3 output).
- Produces: `def load_registry(path: str = "data/merchants.csv") -> Registry` where `Registry` exposes `.categories() -> set[str]`, `.domains_for_category(category: str) -> list[str]`, `.all_domains() -> list[str]`. Task 4 of this phase and Phase 2's orchestrator both consume `.categories()`.

- [ ] **Step 1: Write the loader**

```python
# app/registry.py
import csv
from dataclasses import dataclass, field


@dataclass
class Registry:
    _by_category: dict[str, list[str]] = field(default_factory=dict)

    def categories(self) -> set[str]:
        return set(self._by_category.keys())

    def domains_for_category(self, category: str) -> list[str]:
        return self._by_category.get(category, [])

    def all_domains(self) -> list[str]:
        return [d for domains in self._by_category.values() for d in domains]


def load_registry(path: str = "data/merchants.csv") -> Registry:
    by_category: dict[str, list[str]] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row.get("ok") != "True":
                continue
            by_category.setdefault(row["category"], []).append(row["domain"])
    return Registry(_by_category=by_category)
```

- [ ] **Step 2: Write the test against the real generated file**

```python
# tests/test_registry.py
from app.registry import load_registry


def test_load_registry_from_real_merchants_csv():
    registry = load_registry("data/merchants.csv")
    assert len(registry.categories()) > 0
    assert len(registry.all_domains()) > 0
```

Run: `pytest tests/test_registry.py -v`
Expected: PASS against the real file generated in Phase 0 — this is the live check that the probe output is actually loadable.

- [ ] **Step 3: Replace the orchestrator's hardcoded category set**

In `app/orchestrator.py`, replace `_KNOWN_CATEGORIES = {...}` with:

```python
from app.registry import load_registry

_REGISTRY = load_registry()
```

and update the `gate_identification(result, _KNOWN_CATEGORIES)` call to `gate_identification(result, _REGISTRY.categories())`.

- [ ] **Step 4: Commit**

```bash
git add app/registry.py app/orchestrator.py tests/test_registry.py
git commit -m "feat: merchant registry loader wired into confidence gate"
```

---

### Task 2: Shopify public JSON client

**Files:**
- Create: `app/shopify_client.py`
- Test: `tests/test_shopify_client.py`

**Interfaces:**
- Produces: `async def search_suggest(domain: str, query: str) -> list[dict]` (returns raw product resources) and `async def get_product(domain: str, handle: str) -> dict` (returns full variant list with real prices). Task 4 (resolver) and Task 3 (luna) both consume these.

- [ ] **Step 1: Write the client**

```python
# app/shopify_client.py
import httpx


async def search_suggest(domain: str, query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        try:
            r = await client.get(
                f"https://{domain}/search/suggest.json",
                params={"q": query, "resources[type]": "product", "resources[limit]": 10},
            )
            r.raise_for_status()
            return r.json().get("resources", {}).get("results", {}).get("products", [])
        except Exception:
            return []


async def get_product(domain: str, handle: str) -> dict:
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        r = await client.get(f"https://{domain}/products/{handle}.json")
        r.raise_for_status()
        return r.json()["product"]
```

- [ ] **Step 2: Write the live test against a real store**

```python
# tests/test_shopify_client.py
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
```

Run: `pytest tests/test_shopify_client.py -v`
Expected: both PASS — these hit the real `beminimalist.co` store over the network. If `beminimalist.co` isn't in your verified `merchants.csv`, swap in whichever skincare domain from the file did survive the probe.

- [ ] **Step 3: Commit**

```bash
git add app/shopify_client.py tests/test_shopify_client.py
git commit -m "feat: shopify public json client"
```

---

### Task 3: Luna variant matching

**Files:**
- Create: `app/luna.py`
- Test: `tests/test_luna.py`

**Interfaces:**
- Produces: `class VariantMatch(pydantic.BaseModel)` with `best_match_handle: str | None`, `similarity: float`, `shared_attributes: list[str]`, `differences: list[str]`, `one_line_pitch: str`, and `async def match_variant(identification: Identification, candidates: list[dict]) -> VariantMatch`. Phase 6 (substitution) reuses this exact function against cross-brand candidates.

- [ ] **Step 1: Write the schema and call**

```python
# app/luna.py
from openai import OpenAI
from pydantic import BaseModel

from app.config import settings
from app.vision import Identification

_client = OpenAI(api_key=settings.openai_api_key)


class VariantMatch(BaseModel):
    best_match_handle: str | None
    similarity: float
    shared_attributes: list[str]
    differences: list[str]
    one_line_pitch: str


async def match_variant(
    identification: Identification, candidates: list[dict]
) -> VariantMatch:
    candidate_summary = "\n".join(
        f"- handle={c.get('handle')} title={c.get('title')}" for c in candidates
    )
    response = _client.responses.parse(
        model="gpt-5.6-luna",
        input=[
            {
                "role": "system",
                "content": (
                    "Given a target product identification and a list of candidate "
                    "Shopify products, pick the single best match. `similarity` is "
                    "0-1; below ~0.6 the match isn't worth offering. List concrete "
                    "shared_attributes and differences, not vague ones."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target: brand={identification.brand} "
                    f"product={identification.product} variant={identification.variant}\n"
                    f"Candidates:\n{candidate_summary}"
                ),
            },
        ],
        text_format=VariantMatch,
    )
    return response.output_parsed
```

- [ ] **Step 2: Write the live test**

```python
# tests/test_luna.py
from app.luna import match_variant
from app.vision import Identification


async def test_match_variant_picks_a_handle_from_real_candidates():
    identification = Identification(
        object_type="serum", brand="Minimalist", product="Salicylic Acid Serum",
        variant="2%, 30ml", category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"], confidence=0.95,
        reasoning="clear label", missing_info=None, suggested_photo=None,
    )
    candidates = [
        {"handle": "salicylic-acid-2-serum-30ml", "title": "Salicylic Acid 2% Serum 30ml"},
        {"handle": "niacinamide-serum-30ml", "title": "Niacinamide 10% Serum 30ml"},
    ]
    result = await match_variant(identification, candidates)
    assert result.best_match_handle == "salicylic-acid-2-serum-30ml"
    assert result.similarity > 0.6
```

Run: `pytest tests/test_luna.py -v`
Expected: PASS — real OpenAI call, cheap (`luna` pricing), verifies the model actually distinguishes the right handle from a decoy.

- [ ] **Step 3: Commit**

```bash
git add app/luna.py tests/test_luna.py
git commit -m "feat: gpt-5.6-luna variant matching"
```

---

### Task 4: Resolver — brand-filter routing

**Files:**
- Create: `app/resolver.py`
- Test: `tests/test_resolver.py`

**Interfaces:**
- Consumes: `Registry` (Task 1), `search_suggest`/`get_product` (Task 2), `match_variant` (Task 3), `Identification` (Phase 1).
- Produces: `class Quote(pydantic.BaseModel)` with `merchant: str`, `shopify_variant_id: str`, `price_paise: int`, `handle: str`, and `async def resolve(identification: Identification, registry: Registry) -> Quote | None` (returns `None` if nothing found — Phase 6 treats that as the substitution trigger). Phase 4 consumes `Quote` directly to build the Prava session amount.

- [ ] **Step 1: Write the routing logic per the spec flowchart**

```python
# app/resolver.py
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
        all_candidates.extend((domain, r) for r in results)

    if not all_candidates:
        return None

    if identification.brand and identification.confidence >= BRAND_CONFIDENCE_FLOOR:
        # single-store path: no cross-brand contamination possible, skip re-filtering
        candidates = [c for _, c in all_candidates]
        domain = all_candidates[0][0]
    else:
        # breadth path: only accept an exact brand+product match, never a near-miss
        exact = [
            (d, c) for d, c in all_candidates
            if identification.brand and identification.brand.lower() in c.get("vendor", "").lower()
        ]
        if not exact:
            return None
        candidates = [c for _, c in exact]
        domain = exact[0][0]

    match = await match_variant(identification, candidates)
    if not match.best_match_handle or match.similarity < 0.6:
        return None

    product = await get_product(domain, match.best_match_handle)
    variant = product["variants"][0]
    return Quote(
        merchant=domain,
        shopify_variant_id=str(variant["id"]),
        price_paise=int(float(variant["price"]) * 100),
        handle=match.best_match_handle,
    )
```

- [ ] **Step 2: Write the resolver test (mocked network, real routing-logic assertions)**

```python
# tests/test_resolver.py
from unittest.mock import AsyncMock, patch

from app.registry import Registry
from app.resolver import resolve
from app.vision import Identification

REGISTRY = Registry(_by_category={
    "Beauty & Personal Care/Skin Care": ["beminimalist.co", "other-skin-store.com"],
})


async def test_confident_brand_routes_to_single_store_only():
    identification = Identification(
        object_type="serum", brand="Minimalist", product="Salicylic Acid Serum",
        variant="2%, 30ml", category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"], confidence=0.95,
        reasoning="clear", missing_info=None, suggested_photo=None,
    )
    with patch("app.resolver.search_suggest", AsyncMock(return_value=[
        {"handle": "sal-serum", "title": "Salicylic Acid 2% Serum", "vendor": "Minimalist"}
    ])) as mock_search, \
         patch("app.resolver.match_variant", AsyncMock(return_value=type(
             "M", (), {"best_match_handle": "sal-serum", "similarity": 0.9}
         )())), \
         patch("app.resolver.get_product", AsyncMock(return_value={
             "variants": [{"id": 123, "price": "549.00"}]
         })):
        quote = await resolve(identification, REGISTRY)
    assert quote is not None
    assert quote.merchant == "beminimalist.co"
    assert quote.price_paise == 54900
    mock_search.assert_awaited_once()  # single store only — brand hard filter held


async def test_price_comes_from_shopify_response_not_model():
    identification = Identification(
        object_type="serum", brand="Minimalist", product="Salicylic Acid Serum",
        variant="2%, 30ml", category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"], confidence=0.95,
        reasoning="clear", missing_info=None, suggested_photo=None,
    )
    with patch("app.resolver.search_suggest", AsyncMock(return_value=[
        {"handle": "sal-serum", "title": "Salicylic Acid 2% Serum", "vendor": "Minimalist"}
    ])), \
         patch("app.resolver.match_variant", AsyncMock(return_value=type(
             "M", (), {"best_match_handle": "sal-serum", "similarity": 0.9}
         )())), \
         patch("app.resolver.get_product", AsyncMock(return_value={
             "variants": [{"id": 123, "price": "999.00"}]
         })) as mock_get_product:
        quote = await resolve(identification, REGISTRY)
    assert quote.price_paise == 99900
    mock_get_product.assert_awaited()
```

Run: `pytest tests/test_resolver.py -v`
Expected: both PASS.

- [ ] **Step 3: Wire QUOTED into the orchestrator**

```python
# app/orchestrator.py (append after compose_and_send call, only when state == IDENTIFIED)
from app.resolver import resolve
from app.state_machine import ItemState

    if state == ItemState.IDENTIFIED:
        quote = await resolve(result, _REGISTRY)
        if quote is None:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE items SET state = 'UNBUYABLE', updated_at = now() WHERE id = %s",
                    (item_id,),
                )
            await send_text(chat_id, f"Couldn't find {result.brand} {result.product} anywhere I can buy from.")
        else:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE items SET state = 'QUOTED', merchant = %s,
                       shopify_variant_id = %s, last_price_paise = %s, updated_at = now()
                       WHERE id = %s""",
                    (quote.merchant, quote.shopify_variant_id, quote.price_paise, item_id),
                )
            price_rupees = quote.price_paise / 100
            await send_text(
                chat_id,
                f"{result.brand} {result.product} · {result.variant} · ₹{price_rupees:.0f}",
            )
```

(This UNBUYABLE-on-no-quote path is a stopgap — Phase 6 replaces it with the real `SUBSTITUTE_OFFERED` branch when category exists but brand doesn't.)

- [ ] **Step 4: Commit**

```bash
git add app/resolver.py app/orchestrator.py tests/test_resolver.py
git commit -m "feat: brand-filter resolver reaching QUOTED with real shopify price"
```

---

## Phase Gate — must pass before Phase 4 starts

- [ ] `pytest` passes for all four new test files.
- [ ] **Live test:** text a clean photo of the demo object. Confirm the reply shows brand, product, variant, and a **₹ price**.
- [ ] Manually open the resolved merchant's product page in a browser and confirm the price in the reply matches the live site — this is the check that catches a hallucinated or stale price.
- [ ] Confirm `items.state = 'QUOTED'` in Supabase with `merchant`, `shopify_variant_id`, `last_price_paise` all populated.
- [ ] **Brand-filter regression check:** temporarily photograph (or re-run with a saved test photo of) a product whose brand is *not* in the registry but whose category is — confirm the resolver does **not** silently return a same-category-different-brand item as a `QUOTED` result (it should currently fall to the UNBUYABLE stopgap; Phase 6 turns this into a proper labelled offer).
- [ ] Everything committed.
