# Otto Phase 6: Substitution & Health Block — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 3's UNBUYABLE-on-no-quote stopgap with the real fourth outcome: when the exact brand isn't reachable but the *category* is, offer a labelled cross-brand alternative — except in health/pharmacy, where it's always a hard decline.

**Architecture:** A `NO_SUBSTITUTE_CATEGORIES` gate checked before any substitution search fires; a category-wide fan-out reusing Phase 3's `search_suggest` and Phase 3's `match_variant` (already generic over any candidate list); a consent step (`SUBSTITUTE_OFFERED` → yes/no) reusing Phase 4's text-message routing pattern.

**Tech Stack:** No new dependencies — pure composition of Phases 3 and 4's modules.

## Global Constraints

*(Same as Phase 0. This phase is the direct implementation of two "both non-negotiable" rules from spec §3:)*

- **Never substitute silently.** The offer is explicit and requires an explicit "yes."
- **Never substitute in health/pharmacy.** `NO_SUBSTITUTE_CATEGORIES = {"Health/Pharmacy", "Health/Health Conditions & Concerns"}` — hard block, no exceptions, even if a plausible-looking cross-brand candidate exists.
- **One offer only.** If the user says "no," log it and stop — do not suggest a second alternative.
- A substitute offer must always **name its own downsides** in the message (`differences` from `VariantMatch`), never just assert similarity.

**Prerequisite:** Phase 5 complete and its gate passed.

---

## File Structure

```
app/
├── substitution.py     # NO_SUBSTITUTE_CATEGORIES gate + category fan-out + offer composition
├── resolver.py           # (modified) resolve() now returns a substitution signal instead of None
├── state_machine.py        # (modified) DECLINED_SUB transition already exists as an enum member from Phase 2 — wire real transitions
└── orchestrator.py           # (modified) SUBSTITUTE_OFFERED branch + yes/no consent handling
tests/
└── test_substitution.py
```

---

### Task 1: The gate and the category-wide search

**Files:**
- Create: `app/substitution.py`
- Test: `tests/test_substitution.py`

**Interfaces:**
- Consumes: `Registry` (Phase 3), `search_suggest` (Phase 3), `match_variant`/`VariantMatch` (Phase 3), `Identification` (Phase 1).
- Produces: `NO_SUBSTITUTE_CATEGORIES: set[str]`, `class SubstituteOffer(pydantic.BaseModel)` with `merchant: str`, `handle: str`, `price_paise: int`, `match: VariantMatch`, and `async def find_substitute(identification: Identification, registry: Registry) -> SubstituteOffer | None`. Task 3 of this phase consumes `find_substitute` directly.

- [ ] **Step 1: Write the gate constant and search**

```python
# app/substitution.py
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

    domain = next(d for d, c in all_candidates if c.get("handle") == match.best_match_handle)
    product = await get_product(domain, match.best_match_handle)
    variant = product["variants"][0]
    return SubstituteOffer(
        merchant=domain,
        handle=match.best_match_handle,
        price_paise=int(float(variant["price"]) * 100),
        match=match,
    )
```

- [ ] **Step 2: Write the tests — gate correctness is the priority here**

```python
# tests/test_substitution.py
from unittest.mock import AsyncMock, patch

from app.registry import Registry
from app.substitution import NO_SUBSTITUTE_CATEGORIES, find_substitute
from app.vision import Identification

SKINCARE_REGISTRY = Registry(_by_category={
    "Beauty & Personal Care/Skin Care": ["beminimalist.co"],
})
HEALTH_REGISTRY = Registry(_by_category={
    "Health/Pharmacy": ["kapiva.in"],
})


async def test_health_category_never_substitutes_even_with_good_candidates():
    identification = Identification(
        object_type="tablet bottle", brand="Dolo", product="Paracetamol 650",
        variant="15 tablets", category="Health/Pharmacy",
        search_terms=["paracetamol 650"], confidence=0.9,
        reasoning="clear label", missing_info=None, suggested_photo=None,
    )
    with patch("app.substitution.search_suggest", AsyncMock(return_value=[
        {"handle": "paracetamol-650", "title": "Paracetamol 650mg"}
    ])) as mock_search:
        offer = await find_substitute(identification, HEALTH_REGISTRY)
    assert offer is None
    mock_search.assert_not_called()  # gate must short-circuit before any network call


async def test_skincare_category_offers_labelled_substitute():
    identification = Identification(
        object_type="bottle", brand="Dove", product="Moisturizer",
        variant="50ml", category="Beauty & Personal Care/Skin Care",
        search_terms=["moisturizer fragrance free"], confidence=0.9,
        reasoning="clear label", missing_info=None, suggested_photo=None,
    )
    fake_match = type("M", (), {
        "best_match_handle": "moisturizer-50ml", "similarity": 0.75,
        "shared_attributes": ["moisturizer", "fragrance-free", "50ml"],
        "differences": ["different brand", "has niacinamide"],
        "one_line_pitch": "Same job, different brand, fragrance-free too.",
    })()
    with patch("app.substitution.search_suggest", AsyncMock(return_value=[
        {"handle": "moisturizer-50ml", "title": "Moisturizer 50ml"}
    ])), \
         patch("app.substitution.match_variant", AsyncMock(return_value=fake_match)), \
         patch("app.substitution.get_product", AsyncMock(return_value={
             "variants": [{"id": 1, "price": "399.00"}]
         })):
        offer = await find_substitute(identification, SKINCARE_REGISTRY)
    assert offer is not None
    assert offer.price_paise == 39900
    assert "different brand" in offer.match.differences


async def test_weak_similarity_below_floor_offers_nothing():
    identification = Identification(
        object_type="bottle", brand="Dove", product="Moisturizer",
        variant="50ml", category="Beauty & Personal Care/Skin Care",
        search_terms=["moisturizer"], confidence=0.9,
        reasoning="clear label", missing_info=None, suggested_photo=None,
    )
    fake_match = type("M", (), {
        "best_match_handle": "unrelated-item", "similarity": 0.3,
        "shared_attributes": [], "differences": [], "one_line_pitch": "",
    })()
    with patch("app.substitution.search_suggest", AsyncMock(return_value=[
        {"handle": "unrelated-item", "title": "Something else"}
    ])), \
         patch("app.substitution.match_variant", AsyncMock(return_value=fake_match)):
        offer = await find_substitute(identification, SKINCARE_REGISTRY)
    assert offer is None
```

Run: `pytest tests/test_substitution.py -v`
Expected: all 3 PASS. `test_health_category_never_substitutes_even_with_good_candidates` is the single most important assertion in this phase.

- [ ] **Step 3: Commit**

```bash
git add app/substitution.py tests/test_substitution.py
git commit -m "feat: substitution search with health-category hard block"
```

---

### Task 2: Wire SUBSTITUTE_OFFERED into the resolution path

**Files:**
- Modify: `app/orchestrator.py`

**Interfaces:**
- Consumes: `find_substitute` (Task 1); `resolve` (Phase 3) — unchanged signature, still returns `None` on no exact match.
- Produces: the `IDENTIFIED`-branch logic in `handle_photo_message` now tries `resolve` first, and on `None` tries `find_substitute` before falling back to `UNBUYABLE`.

- [ ] **Step 1: Replace the Phase 3 stopgap**

```python
# app/orchestrator.py (replace the `if quote is None:` branch from Phase 3 Task 4 Step 3)
from app.substitution import find_substitute

    if state == ItemState.IDENTIFIED:
        quote = await resolve(result, _REGISTRY)
        if quote is not None:
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
        else:
            offer = await find_substitute(result, _REGISTRY)
            if offer is None:
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE items SET state = 'UNBUYABLE', updated_at = now() WHERE id = %s",
                        (item_id,),
                    )
                await send_text(chat_id, f"Couldn't get {result.brand} {result.product} — none of my merchants stock it.")
            else:
                with get_conn() as conn, conn.cursor() as cur:
                    cur.execute(
                        """UPDATE items SET state = 'SUBSTITUTE_OFFERED', merchant = %s,
                           shopify_variant_id = %s, last_price_paise = %s, updated_at = now()
                           WHERE id = %s""",
                        (offer.merchant, offer.handle, offer.price_paise, item_id),
                    )
                differences = ", ".join(offer.match.differences)
                await send_text(
                    chat_id,
                    f"Can't get {result.brand} one — none of my merchants stock it. "
                    f"Closest is {offer.match.one_line_pitch} ₹{offer.price_paise/100:.0f}. "
                    f"{differences}. Want it?",
                )
```

- [ ] **Step 2: Add yes/no consent handling to `handle_text_message`**

```python
# app/orchestrator.py (handle_text_message, add before the "refill" branch from Phase 5)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.id FROM items i JOIN users u ON u.id = i.user_id
               WHERE u.phone = %s AND i.state = 'SUBSTITUTE_OFFERED'
               ORDER BY i.updated_at DESC LIMIT 1""",
            (user_phone,),
        )
        sub_row = cur.fetchone()

    if sub_row is not None and text_lower in ("yes", "no"):
        item_id = sub_row[0]
        new_state = "IDENTIFIED" if text_lower == "yes" else "DECLINED_SUB"
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET state = %s, updated_at = now() WHERE id = %s",
                (new_state, item_id),
            )
            cur.execute(
                "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'substitute_response', %s)",
                (item_id, json.dumps({"accepted": text_lower == "yes"})),
            )
        if text_lower == "no":
            await send_text(chat_id, "No worries — logged as a miss.")
            return
        # "yes": item is back to IDENTIFIED with merchant/variant/price already populated
        # from the SUBSTITUTE_OFFERED state; re-quote it directly to QUOTED without
        # re-running resolve() (that would re-search and could pick a different item).
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE items SET state = 'QUOTED', updated_at = now()
                   WHERE id = %s RETURNING brand, product, merchant, last_price_paise""",
                (item_id,),
            )
            brand, product, merchant, price_paise = cur.fetchone()
        await send_text(chat_id, f"Got it — {brand} {product} · ₹{price_paise/100:.0f}. Reply 'yes' to buy.")
        return
```

This routes an accepted substitute through the exact same `QUOTED` → "yes" → `AWAITING_APPROVAL` path Phase 4 already built — no new payment code needed.

- [ ] **Step 3: Commit**

```bash
git add app/orchestrator.py
git commit -m "feat: wire substitute-offered branch and consent flow into orchestrator"
```

---

## Phase Gate — must pass before Phase 7 starts

- [ ] `pytest` passes for `tests/test_substitution.py`.
- [ ] **Live test 1 (substitution offer):** text a photo of a product whose brand is *not* in the registry but whose category is (e.g. a Dove or other non-registry skincare item). Confirm the reply names a specific alternative, its price, and explicitly states what's different (not just "similar item found"). Confirm `items.state = 'SUBSTITUTE_OFFERED'`.
- [ ] **Live test 2 (accept):** reply "yes". Confirm it proceeds into the existing QUOTED → purchase flow from Phase 4 (you do not need to complete a full payment here to pass this gate — confirming the `QUOTED` reply and state is enough, to conserve test-card transactions per the daily cap noted in Phase 4).
- [ ] **Live test 3 (decline):** repeat with a fresh item, reply "no". Confirm "logged as a miss" reply and `items.state = 'DECLINED_SUB'`. Confirm no second alternative is offered.
- [ ] **Live test 4 (health hard block):** text a photo of a health/pharmacy item whose exact brand isn't in the registry (e.g. a supplement bottle). Confirm the reply is a plain `UNBUYABLE` decline — never a substitution offer — regardless of how good a same-category candidate might exist.
- [ ] Everything committed.
