# Phase 3 Task 4 Report

Implemented `app.resolver.Quote` and `resolve()` with a strict confident-brand
single-domain route, exact-brand validation on broader searches, and Shopify
variant prices converted to paise.

`handle_photo_message()` now resolves IDENTIFIED items after composing the
identification reply. A quote persists QUOTED merchant, variant, and price
data; no quote persists UNBUYABLE and sends the stopgap response.

Added resolver unit tests for the brand hard filter, Shopify-derived price, and
missing variant match, plus orchestration coverage for QUOTED and UNBUYABLE.

Verification: `.venv/bin/pytest -v` — 45 passed (3 existing dependency warnings).

## Review remediation

- Enforced case-insensitive vendor/brand substring matching on the confident
  single-store route as well as fan-out routes.
- Retained each candidate's source domain through variant selection, then fetch
  the winning handle from its source domain.
- Added regression coverage for wrong-vendor rejection and a winner from the
  second fan-out domain.

Evidence: `.venv/bin/pytest tests/test_resolver.py tests/test_registry.py -q`
— 7 passed. Full suite: 42 passed, 5 failed because sandboxed external
Supabase/OpenAI/Shopify requests could not connect; failures are unrelated to
the resolver changes.

## High finding remediation

- Narrowed `_brand_matches()` to require the normalized brand to be contained
  in the normalized vendor; a vendor that is merely a brand prefix no longer
  passes the hard filter.
- Added regression coverage proving `brand="Aesop"` with `vendor="A"` returns
  no quote, while `vendor="Minimalist India"` remains accepted for
  `brand="Minimalist"`.

Evidence: `.venv/bin/pytest tests/test_resolver.py -q` — 6 passed.

## Critical/High branch-review remediation

- Added Luna's structured `ShopifyVariantMatch` selection after the Shopify
  product lookup. For multi-variant products, `resolve()` accepts only an id
  returned by Luna that exists in Shopify's response, and the quote's id and
  paise price come exclusively from that selected Shopify variant. Single
  variants avoid the additional model call.
- Replaced sequential domain lookup with failure-isolated `asyncio.gather`;
  one failed merchant lookup no longer aborts category/global fan-out.
- Documented `SINGLE_STORE_SIMILARITY_FLOOR = 0.6` and
  `BREADTH_SIMILARITY_FLOOR = 0.75`; broad searches now require the higher
  threshold. Brand filtering remains mandatory, so unknown brands still return
  no quote rather than crossing vendors.
- Added regression coverage for a non-index-zero Shopify variant, breadth
  rejection at similarity 0.65 while the single-store route accepts it, and
  failed-domain fan-out isolation.

Evidence: `.venv/bin/pytest tests/test_resolver.py -q` — 9 passed.
Required combined command was run: `.venv/bin/pytest tests/test_resolver.py
tests/test_luna.py -q` — 10 passed.
