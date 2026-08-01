# Task 3 Report — QUOTED to AWAITING_APPROVAL

## Implemented

- Added `handle_text_message`, which accepts explicit purchase confirmations for
  the most recent `QUOTED` item, creates a Prava session with the stored
  `last_price_paise`, transitions the item to `AWAITING_APPROVAL`, records the
  purchase and chat reference, then sends the price notice and approval URL as
  two separate Linq messages.
- Routed Linq text parts through `handle_text_message` without changing the
  existing production webhook payload extraction.
- Completed payment finalization chat lookup and its Ordered/Failed notification.

## Tests and review

- Focused orchestration, webhook, and callback tests passed: `15 passed`.
- Linter diagnostics found no errors and `git diff --check` passed.
- `tests/test_prava.py tests/test_prava_callback.py` reached the live Prava
  sandbox, but its two session-creation tests received `500 Internal Server
  Error` from `https://sandbox.api.prava.space/v1/sessions`; the two callback
  tests passed. A real end-to-end sandbox payment was therefore not completed.
# Task 3 Report: Luna variant matching

## Status
**Complete.** `app/luna.py` exposes `VariantMatch` and `match_variant()` using `gpt-5.6-luna` structured output. Sync OpenAI call wrapped in `asyncio.to_thread` to keep async signature.

## Commits
- `35461e6` — `feat: gpt-5.6-luna variant matching`
  - `app/luna.py` (new)
  - `tests/test_luna.py` (new)

## Tests
| Suite | Result |
|-------|--------|
| `tests/test_luna.py` | 1/1 PASS (live network, ~5.05s) |

- `test_match_variant_picks_a_handle_from_real_candidates` — picks `salicylic-acid-2-serum-30ml` over niacinamide decoy, `similarity > 0.6`

## Implementation notes
- `_match_variant_sync()` builds candidate summary from handle/title; `match_variant()` delegates via `asyncio.to_thread`.
- Same interface reused by Phase 6 substitution against cross-brand candidates.

## Concerns
- Live test depends on OpenAI `gpt-5.6-luna` availability and consistent structured output.
- No fallback model on rate limit (unlike vision sol→terra); acceptable at current scale but may need parity later.

## Not in scope (per brief)
- Resolver fan-out wiring — Phase 3 Task 4.

## Phase 4 branch-review fixes

- Payment finalization now requires Prava status `awaiting_result` plus token,
  dynamic CVV, and transaction reference before marking a purchase paid,
  ordering the item, or reporting `APPROVED`. Non-ready results fail; a
  `DECLINED` report is sent only when a transaction reference exists.
- Confirmation now atomically claims the latest `QUOTED` item before creating
  a Prava session. Session failures return the item to `QUOTED`, and duplicate
  confirmations no-op after the first claim.
- Replaced the live-network Prava tests with mocked HTTP tests.
- Evidence: `uv run pytest tests/test_orchestrator.py tests/test_prava_callback.py tests/test_prava.py`
  completed with `18 passed` (one pre-existing FastAPI/httpx deprecation warning).
