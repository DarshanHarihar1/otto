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
