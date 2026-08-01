# Otto Phase 1: Vision Identification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A photo texted to Otto gets downloaded, archived, and identified by `gpt-5.6-sol` via structured output, with the result logged to `items`/`events`.

**Architecture:** Extend the Phase 0 webhook background task: on an inbound message with a media part, download the image bytes, call OpenAI's Responses API with the exact identification schema from the spec, persist the result.

**Tech Stack:** `openai` Python SDK (Responses API, structured outputs), `httpx` for media download, Supabase Storage for the photo archive.

## Global Constraints

*(Same as Phase 0 — see `2026-08-01-otto-phase-0-foundation.md`. Highlights that apply directly to this phase:)*

- Structured outputs only — never let free text become a purchase or gate a state transition.
- No memory framework — identification results go straight into the `items`/`events` Postgres tables from Phase 0.
- Vision is the one call the spec says not to cheap out on: use `gpt-5.6-sol`, not `luna` or `terra`.

**Prerequisite:** Phase 0 complete and its gate passed (webhook live, DB reachable, tunnel up).

---

## File Structure

```
app/
├── media.py          # download inbound Linq media to bytes + archive to Supabase Storage
├── vision.py          # gpt-5.6-sol identification call, structured schema
├── orchestrator.py    # RECEIVED -> IDENTIFYING state transition, ties webhook to vision
└── routes/
    └── webhook.py      # (modified) route message.received-with-media to orchestrator
tests/
├── test_media.py
├── test_vision.py
└── test_orchestrator.py
```

---

### Task 1: Media downloader + Storage archive

**Files:**
- Create: `app/media.py`
- Test: `tests/test_media.py`

**Interfaces:**
- Consumes: `settings.openai_api_key`, `settings.supabase_db_url` (Phase 0 `app.config.settings`).
- Produces: `async def download_media(url: str) -> bytes` and `def archive_photo(item_id: str, image_bytes: bytes) -> str` (returns the storage path). Task 3 of this phase calls both.

- [ ] **Step 1: Write the downloader**

```python
# app/media.py
import httpx
from supabase import create_client

from app.config import settings

_storage_client = create_client(settings.supabase_db_url, settings.openai_api_key)  # placeholder client init replaced in Step 2


async def download_media(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content
```

- [ ] **Step 2: Fix Storage client init and add archive function**

Supabase Storage needs the project URL + service-role key, not the Postgres DSN — add two settings fields in `app/config.py` (`supabase_url`, `supabase_service_key`) mirroring Task 1/Step 3 of Phase 0's config pattern, then:

```python
# app/media.py (replace the placeholder client line)
from supabase import create_client
from app.config import settings

_storage = create_client(settings.supabase_url, settings.supabase_service_key)


def archive_photo(item_id: str, image_bytes: bytes) -> str:
    path = f"{item_id}.jpg"
    _storage.storage.from_("photos").upload(
        path, image_bytes, {"content-type": "image/jpeg", "upsert": "true"}
    )
    return path
```

Add to `.env.example` and `.env`: `SUPABASE_URL=`, `SUPABASE_SERVICE_KEY=`. Add `supabase>=2.9` to `pyproject.toml` dependencies.

Create the `photos` bucket once, manually, in the Supabase dashboard (Storage → New bucket → name `photos`, private).

- [ ] **Step 3: Write the test (mocked HTTP, real Storage call)**

```python
# tests/test_media.py
import respx
from httpx import Response

from app.media import download_media, archive_photo


@respx.mock
async def test_download_media_returns_bytes():
    respx.get("https://cdn.linqapp.com/x.jpg").mock(
        return_value=Response(200, content=b"fakejpegbytes")
    )
    data = await download_media("https://cdn.linqapp.com/x.jpg")
    assert data == b"fakejpegbytes"


def test_archive_photo_uploads_and_returns_path():
    path = archive_photo("test-item-id", b"fakejpegbytes")
    assert path == "test-item-id.jpg"
```

Run: `pytest tests/test_media.py -v`
Expected: `test_download_media_returns_bytes` PASS (mocked). `test_archive_photo_uploads_and_returns_path` PASS against the **real** Supabase Storage bucket — this is the live check for this task; confirm the file appears in the dashboard under Storage → photos.

- [ ] **Step 4: Commit**

```bash
git add app/media.py app/config.py .env.example pyproject.toml tests/test_media.py
git commit -m "feat: media download and photo archive"
```

---

### Task 2: Vision identification call

**Files:**
- Create: `app/vision.py`
- Test: `tests/test_vision.py`

**Interfaces:**
- Consumes: `settings.openai_api_key`.
- Produces: `async def identify(image_bytes: bytes) -> Identification` where `Identification` is a `pydantic.BaseModel` with fields `object_type: str`, `brand: str | None`, `product: str | None`, `variant: str | None`, `category: str | None`, `search_terms: list[str]`, `confidence: float`, `reasoning: str`, `missing_info: str | None`, `suggested_photo: str | None`. Phase 2 and Phase 3 both import this type and function.

- [ ] **Step 1: Define the schema and the call**

```python
# app/vision.py
import base64

from openai import OpenAI
from pydantic import BaseModel

from app.config import settings

_client = OpenAI(api_key=settings.openai_api_key)


class Identification(BaseModel):
    object_type: str
    brand: str | None
    product: str | None
    variant: str | None
    category: str | None
    search_terms: list[str]
    confidence: float
    reasoning: str
    missing_info: str | None
    suggested_photo: str | None


_SYSTEM_PROMPT = (
    "You identify a physical retail product from a photo, typically an empty "
    "or near-empty container the user wants to repurchase. Return brand, exact "
    "product name, and variant (size/shade/concentration/count) as precisely as "
    "the label allows. If you cannot read a detail confidently, leave it null, "
    "explain what's missing in `missing_info`, and name the exact photo angle "
    "that would resolve it in `suggested_photo`. `confidence` reflects how sure "
    "you are of brand+product+variant together, not just object_type."
)


async def identify(image_bytes: bytes) -> Identification:
    b64 = base64.b64encode(image_bytes).decode()
    response = _client.responses.parse(
        model="gpt-5.6-sol",
        input=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Identify this product."},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}",
                    },
                ],
            },
        ],
        text_format=Identification,
    )
    return response.output_parsed
```

- [ ] **Step 2: Write the live test against a real photo**

```python
# tests/test_vision.py
from pathlib import Path

from app.vision import identify

FIXTURE = Path(__file__).parent / "fixtures" / "minimalist_serum.jpg"


async def test_identify_real_product_photo():
    image_bytes = FIXTURE.read_bytes()
    result = await identify(image_bytes)
    assert result.brand is not None
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasoning
```

Take a real photo of the demo object (Minimalist serum bottle per the spec's recommendation) on your phone, save it to `tests/fixtures/minimalist_serum.jpg`.

Run: `pytest tests/test_vision.py -v`
Expected: PASS. Print `result` manually once (`python -c "..."`) and eyeball that `brand == "Minimalist"`, `confidence` is high (front label clean shot) — this is the live integration check; a mocked test would not catch a bad prompt or wrong schema mapping.

- [ ] **Step 3: Commit**

```bash
git add app/vision.py tests/test_vision.py tests/fixtures/minimalist_serum.jpg
git commit -m "feat: gpt-5.6-sol vision identification"
```

---

### Task 3: Wire into the webhook — RECEIVED → IDENTIFYING

**Files:**
- Create: `app/orchestrator.py`
- Modify: `app/routes/webhook.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `download_media`, `archive_photo` (Task 1); `identify` (Task 2); `get_conn` (Phase 0 Task 2); `send_text` (Phase 0 Task 4).
- Produces: `async def handle_photo_message(user_phone: str, chat_id: str, media_url: str) -> str` — returns the created `item_id`. Phase 2's state machine extends this same function.

- [ ] **Step 1: Write the orchestrator entry point**

```python
# app/orchestrator.py
import json
import uuid

from app.db import get_conn
from app.media import archive_photo, download_media
from app.routes.webhook import send_text
from app.vision import identify


async def handle_photo_message(user_phone: str, chat_id: str, media_url: str) -> str:
    item_id = str(uuid.uuid4())
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE phone = %s", (user_phone,))
        row = cur.fetchone()
        user_id = row[0]
        cur.execute(
            "INSERT INTO items (id, user_id, state) VALUES (%s, %s, 'IDENTIFYING')",
            (item_id, user_id),
        )

    image_bytes = await download_media(media_url)
    storage_path = archive_photo(item_id, image_bytes)
    result = await identify(image_bytes)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE items
            SET brand = %s, product = %s, variant = %s, category = %s,
                confidence = %s, photo_storage_path = %s, updated_at = now()
            WHERE id = %s
            """,
            (
                result.brand,
                result.product,
                result.variant,
                result.category,
                result.confidence,
                storage_path,
                item_id,
            ),
        )
        cur.execute(
            "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'identified', %s)",
            (item_id, json.dumps(result.model_dump())),
        )

    await send_text(
        chat_id,
        f"Got it — {result.brand or 'unknown brand'} {result.product or ''}. "
        f"({result.reasoning})",
    )
    return item_id
```

Note: the confidence gate, `NEEDS_ANGLE` reply, and proper state transitions are Phase 2's job — this task's reply is intentionally a plain debug echo so this phase can be verified in isolation.

- [ ] **Step 2: Route the webhook to the orchestrator**

Modify `app/routes/webhook.py`'s `_handle_message_received`:

```python
# app/routes/webhook.py (replace _handle_message_received)
import asyncio

from app.orchestrator import handle_photo_message


def _handle_message_received(payload: dict) -> None:
    data = payload["data"]
    chat_id = data["chat_id"]
    phone = data["sender_phone"]
    media = data.get("media", [])
    if media:
        asyncio.run(handle_photo_message(phone, chat_id, media[0]["url"]))
    else:
        asyncio.run(send_text(chat_id, "got your message"))
```

(Field names `sender_phone` / `media[0]["url"]` — confirm against the actual `message.received` payload shape from the first real webhook delivery in Phase 0's gate; adjust here if Linq uses different keys. This is exactly Open Question 6 from the spec.)

- [ ] **Step 3: Write the orchestrator test**

```python
# tests/test_orchestrator.py
from unittest.mock import AsyncMock, patch

from app.orchestrator import handle_photo_message
from app.vision import Identification


async def test_handle_photo_message_creates_and_updates_item():
    fake_result = Identification(
        object_type="serum bottle",
        brand="Minimalist",
        product="Salicylic Acid 2% Serum",
        variant="30ml",
        category="Beauty & Personal Care/Skin Care",
        search_terms=["salicylic acid 2% serum"],
        confidence=0.95,
        reasoning="Clear front label with brand and concentration visible.",
        missing_info=None,
        suggested_photo=None,
    )
    with patch("app.orchestrator.download_media", AsyncMock(return_value=b"bytes")), \
         patch("app.orchestrator.archive_photo", return_value="path.jpg"), \
         patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)), \
         patch("app.orchestrator.send_text", AsyncMock()) as mock_send:
        item_id = await handle_photo_message("+91XXXXXXXXXX", "chat1", "https://x/y.jpg")
    assert item_id
    mock_send.assert_awaited_once()
```

Run: `pytest tests/test_orchestrator.py -v`
Expected: PASS (mocked — this test proves the wiring, not the real APIs; those were already proven live in Tasks 1 and 2).

- [ ] **Step 4: Commit**

```bash
git add app/orchestrator.py app/routes/webhook.py tests/test_orchestrator.py
git commit -m "feat: wire vision identification into webhook pipeline"
```

---

## Phase Gate — must pass before Phase 2 starts

- [ ] `pytest` passes for `tests/test_media.py`, `tests/test_vision.py`, `tests/test_orchestrator.py`.
- [ ] **Live test:** with the server running and tunnel up, text a real photo of the demo object (Minimalist serum bottle) to the Linq sandbox number from your phone.
- [ ] Confirm a reply arrives in iMessage naming the brand and product.
- [ ] Confirm in Supabase: a new `items` row exists with `state = 'IDENTIFYING'`, `brand`, `product`, `confidence` populated, and `photo_storage_path` set.
- [ ] Confirm in Supabase Storage: the uploaded photo is visible under the `photos` bucket at that path.
- [ ] Confirm an `events` row with `kind = 'identified'` and the full structured JSON in `payload`.
- [ ] Everything committed; `git log --oneline` shows the 3 new commits for this phase on top of Phase 0's.
