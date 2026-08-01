## Fixes

- Wrapped the photo pipeline in a single error boundary. It logs the original exception with traceback, attempts the user-facing front-label prompt, and swallows the failure after replying to avoid duplicate BackgroundTasks logging.
- Added explicit errors for an unknown user and a missing parsed vision result before any item update.
- Moved synchronous OpenAI parsing and photo archival off the event loop with `asyncio.to_thread`.
- Added INFO routing logs with each incoming part's type and keys, without logging media URLs.
- Added regression coverage for the two pipeline error paths and confirmed media routing does not echo text.

## Test evidence

- `.venv/bin/python -m pytest tests/test_orchestrator.py tests/test_webhook.py`: 15 passed.
- `.venv/bin/python -m pytest`: 16 passed; 3 live-network failures: Supabase DB DNS resolution (`test_db.py`), Supabase storage proxy 403 (`test_media.py`), and OpenAI connection (`test_vision.py`).
- IDE lints: no errors in edited files.

## Phase 2 critical fixes

- Created `app.categories.load_known_categories()` and made both orchestrator and vision use it. It reads the shipped `data/merchants_source.csv` (`Category` column), with compatibility for a future generated `data/merchants.csv` (`category` and optional `ok` columns).
- Vision now receives the shared category registry and its structured-output system prompt requires an exact registry string for `category`, otherwise `null`.
- `category=None` is now `NEEDS_ANGLE`; only a confident, non-null category absent from the registry is `UNBUYABLE`. Gate decisions log state, confidence, and category at INFO.
- Typing starts before media download, uses POST without a body to start and DELETE to stop, is stopped before reply composition, and failures are logged without blocking the successful reply.
- IDENTIFIED replies use safe fallbacks rather than rendering `None`.

## Phase 2 test evidence

`uv run --extra dev pytest -q tests/test_state_machine.py tests/test_reply_composer.py tests/test_orchestrator.py tests/test_vision.py -k 'not real_product_photo'`

Result: `17 passed, 1 deselected` (the deselected vision test requires a live network/API call).

The 10-minute `NEEDS_ANGLE` reuse matching remains unchanged, per scope. Live-gate ordering should be revisited before that matching is expanded.
