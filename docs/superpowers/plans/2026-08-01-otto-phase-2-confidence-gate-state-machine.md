# Otto Phase 2: Confidence Gate & State Machine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 1's debug echo with the real state machine: confidence below threshold triggers `NEEDS_ANGLE` (asks for a specific angle, not "try again"); category absent from the registry triggers `UNBUYABLE` (honest decline); confident + known category reaches `IDENTIFIED`.

**Architecture:** An explicit state enum + transition table mirroring the spec's `stateDiagram-v2`, a reply composer that owns exact copy and typing-indicator wrapping, and a modification to `handle_photo_message` to route through the gate instead of always replying with a debug string.

**Tech Stack:** Pure Python (enum + dataclass), reuses Phase 0/1 modules.

## Global Constraints

*(Same as Phase 0. Highlights for this phase:)*

- Structured outputs only — the confidence gate reads `Identification.confidence`, never re-parses free text.
- Never cut features 2, 3, or 7 from the spec's priority table (confidence gate, clarification loop, graceful decline) — these are explicitly "never cut" items.
- `NEEDS_ANGLE` must ask a *specific* angle (`suggested_photo` from the vision schema), not a generic "try again."

**Prerequisite:** Phase 1 complete and its gate passed.

---

## File Structure

```
app/
├── state_machine.py     # ItemState enum + transition guards
├── reply_composer.py     # typing indicator + exact copy per state
└── orchestrator.py        # (modified) route through gate instead of debug echo
tests/
├── test_state_machine.py
└── test_reply_composer.py
```

---

### Task 1: State machine module

**Files:**
- Create: `app/state_machine.py`
- Test: `tests/test_state_machine.py`

**Interfaces:**
- Produces: `class ItemState(str, Enum)` with members `RECEIVED, IDENTIFYING, NEEDS_ANGLE, UNBUYABLE, SUBSTITUTE_OFFERED, IDENTIFIED, DECLINED_SUB, QUOTED, AWAITING_APPROVAL, PAID, ORDERED, FAILED, EXPIRED, ABANDONED`, and `def gate_identification(result: Identification, category_registry: set[str]) -> ItemState` returning one of `NEEDS_ANGLE`, `UNBUYABLE`, or `IDENTIFIED`. Phase 3 consumes `ItemState.IDENTIFIED` as its entry point; Phase 6 consumes `UNBUYABLE`/`SUBSTITUTE_OFFERED` logic built on top of this gate.

- [ ] **Step 1: Write the enum matching the spec's stateDiagram-v2**

```python
# app/state_machine.py
from enum import Enum

from app.config import settings
from app.vision import Identification


class ItemState(str, Enum):
    RECEIVED = "RECEIVED"
    IDENTIFYING = "IDENTIFYING"
    NEEDS_ANGLE = "NEEDS_ANGLE"
    UNBUYABLE = "UNBUYABLE"
    SUBSTITUTE_OFFERED = "SUBSTITUTE_OFFERED"
    IDENTIFIED = "IDENTIFIED"
    DECLINED_SUB = "DECLINED_SUB"
    QUOTED = "QUOTED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    PAID = "PAID"
    ORDERED = "ORDERED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    ABANDONED = "ABANDONED"


def gate_identification(
    result: Identification, category_registry: set[str]
) -> ItemState:
    if result.confidence < settings.confidence_threshold:
        return ItemState.NEEDS_ANGLE
    if result.category not in category_registry:
        return ItemState.UNBUYABLE
    return ItemState.IDENTIFIED
```

`category_registry` is passed in rather than imported, so this module has no dependency on Phase 3's registry loader yet — Phase 3 wires the real set in.

- [ ] **Step 2: Write the gate tests**

```python
# tests/test_state_machine.py
from app.state_machine import ItemState, gate_identification
from app.vision import Identification

REGISTRY = {"Beauty & Personal Care/Skin Care", "Health/Pharmacy"}


def _result(confidence, category):
    return Identification(
        object_type="bottle",
        brand="Minimalist",
        product="Serum",
        variant="30ml",
        category=category,
        search_terms=["serum"],
        confidence=confidence,
        reasoning="clear label",
        missing_info=None,
        suggested_photo=None,
    )


def test_low_confidence_triggers_needs_angle():
    state = gate_identification(
        _result(0.5, "Beauty & Personal Care/Skin Care"), REGISTRY
    )
    assert state == ItemState.NEEDS_ANGLE


def test_unknown_category_triggers_unbuyable():
    state = gate_identification(_result(0.95, "Electronics/Laptops"), REGISTRY)
    assert state == ItemState.UNBUYABLE


def test_confident_known_category_is_identified():
    state = gate_identification(
        _result(0.95, "Beauty & Personal Care/Skin Care"), REGISTRY
    )
    assert state == ItemState.IDENTIFIED
```

Run: `pytest tests/test_state_machine.py -v`
Expected: all 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add app/state_machine.py tests/test_state_machine.py
git commit -m "feat: item state machine and confidence gate"
```

---

### Task 2: Reply composer — exact copy + typing indicator

**Files:**
- Create: `app/reply_composer.py`
- Test: `tests/test_reply_composer.py`

**Interfaces:**
- Consumes: `send_text` (Phase 0), `ItemState` (Task 1), `Identification` (Phase 1).
- Produces: `async def send_typing(chat_id: str, on: bool) -> None` and `async def compose_and_send(chat_id: str, state: ItemState, result: Identification) -> None`. Phases 4–7 extend this module with additional state→copy branches rather than duplicating send logic.

- [ ] **Step 1: Write typing indicator call**

```python
# app/reply_composer.py
import httpx

from app.config import settings
from app.routes.webhook import LINQ_BASE, send_text
from app.state_machine import ItemState
from app.vision import Identification


async def send_typing(chat_id: str, on: bool) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{LINQ_BASE}/chats/{chat_id}/typing",
            headers={"Authorization": f"Bearer {settings.linq_api_token}"},
            json={"typing": on},
            timeout=10,
        )
```

- [ ] **Step 2: Write copy for NEEDS_ANGLE and UNBUYABLE (exact strings from spec §4)**

```python
# app/reply_composer.py (append)
async def compose_and_send(
    chat_id: str, state: ItemState, result: Identification
) -> None:
    await send_typing(chat_id, True)
    if state == ItemState.NEEDS_ANGLE:
        angle = result.suggested_photo or "the front label, straight-on"
        text = (
            f"Fairly sure that's {result.brand or 'this'} "
            f"{result.product or 'product'}, but I can't read "
            f"{result.missing_info or 'a detail'}. Photo of {angle}?"
        )
    elif state == ItemState.UNBUYABLE:
        text = (
            f"That's {result.brand or ''} {result.product or 'this item'}. "
            f"I can't buy that — it's not something I can get through the "
            f"merchants I use."
        )
    elif state == ItemState.IDENTIFIED:
        text = f"Got it — {result.brand} {result.product} ({result.variant}). Looking it up."
    else:
        text = f"({state.value})"
    await send_typing(chat_id, False)
    await send_text(chat_id, text)
```

- [ ] **Step 3: Write the test**

```python
# tests/test_reply_composer.py
from unittest.mock import AsyncMock, patch

from app.reply_composer import compose_and_send
from app.state_machine import ItemState
from app.vision import Identification


async def test_needs_angle_asks_specific_angle():
    result = Identification(
        object_type="bottle", brand="Minimalist", product="Serum", variant=None,
        category="Beauty & Personal Care/Skin Care", search_terms=["serum"],
        confidence=0.5, reasoning="blurry", missing_info="the concentration %",
        suggested_photo="the front label, straight-on",
    )
    with patch("app.reply_composer.send_typing", AsyncMock()), \
         patch("app.reply_composer.send_text", AsyncMock()) as mock_send:
        await compose_and_send("chat1", ItemState.NEEDS_ANGLE, result)
    text = mock_send.call_args.args[1]
    assert "front label" in text
    assert "try again" not in text.lower()


async def test_unbuyable_names_the_item():
    result = Identification(
        object_type="laptop", brand="Apple", product="MacBook Pro 14\"",
        variant=None, category="Electronics/Laptops", search_terms=[],
        confidence=0.9, reasoning="clear", missing_info=None, suggested_photo=None,
    )
    with patch("app.reply_composer.send_typing", AsyncMock()), \
         patch("app.reply_composer.send_text", AsyncMock()) as mock_send:
        await compose_and_send("chat1", ItemState.UNBUYABLE, result)
    text = mock_send.call_args.args[1]
    assert "MacBook Pro" in text
    assert "can't buy" in text
```

Run: `pytest tests/test_reply_composer.py -v`
Expected: both PASS.

- [ ] **Step 4: Commit**

```bash
git add app/reply_composer.py tests/test_reply_composer.py
git commit -m "feat: reply composer with typing indicator and state copy"
```

---

### Task 3: Wire the gate into the orchestrator

**Files:**
- Modify: `app/orchestrator.py`

**Interfaces:**
- Consumes: `gate_identification` (Task 1), `compose_and_send` (Task 2).
- Produces: `handle_photo_message` now writes the real `state` column and sends state-appropriate copy instead of the Phase 1 debug echo. Phase 3 picks up from `ItemState.IDENTIFIED`.

- [ ] **Step 1: Reuse an open `NEEDS_ANGLE` item instead of creating a new one**

Per spec Open Question 7, a follow-up photo sent while an item is mid-clarification should refine that same item, not start a new one — otherwise a `NEEDS_ANGLE` reply followed by the requested angle photo produces two disconnected `items` rows instead of one resolved item. Spec's stated answer: "item stays open for 10 minutes; any photo in that window is a refinement."

Replace the item-creation block at the top of `handle_photo_message` (written in Phase 1 Task 3 Step 1):

```python
# app/orchestrator.py (replace the item_id/INSERT block at the top of handle_photo_message)
async def handle_photo_message(user_phone: str, chat_id: str, media_url: str) -> str:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE phone = %s", (user_phone,))
        user_id = cur.fetchone()[0]
        cur.execute(
            """SELECT id FROM items
               WHERE user_id = %s AND state = 'NEEDS_ANGLE'
                 AND updated_at > now() - interval '10 minutes'
               ORDER BY updated_at DESC LIMIT 1""",
            (user_id,),
        )
        open_item = cur.fetchone()
        if open_item:
            item_id = open_item[0]
        else:
            item_id = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO items (id, user_id, state) VALUES (%s, %s, 'IDENTIFYING')",
                (item_id, user_id),
            )
```

The rest of the function (download, archive, identify) is unchanged — it already updates by `item_id`, so reusing the id is sufficient to make the second photo overwrite the first's guess rather than fork into a new row.

- [ ] **Step 2: Replace the tail of `handle_photo_message`**

```python
# app/orchestrator.py (replace everything after the UPDATE/INSERT block)
from app.reply_composer import compose_and_send
from app.state_machine import ItemState, gate_identification

# NOTE: hardcoded until Phase 3's registry loader exists.
_KNOWN_CATEGORIES = {
    "Beauty & Personal Care/Skin Care",
    "Health/Pharmacy",
    "Health/Health Conditions & Concerns",
}

    # ... after building `result` and archiving the photo:
    state = gate_identification(result, _KNOWN_CATEGORIES)

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE items
            SET brand = %s, product = %s, variant = %s, category = %s,
                confidence = %s, photo_storage_path = %s, state = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (
                result.brand, result.product, result.variant, result.category,
                result.confidence, storage_path, state.value, item_id,
            ),
        )
        cur.execute(
            "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'identified', %s)",
            (item_id, json.dumps(result.model_dump())),
        )

    await compose_and_send(chat_id, state, result)
    return item_id
```

`_KNOWN_CATEGORIES` is a placeholder set matching what Phase 0's merchant registry actually contains — Phase 3 replaces this with `app.registry.categories()` loaded from `data/merchants.csv`.

- [ ] **Step 2: Update the orchestrator test for the new state-aware path**

```python
# tests/test_orchestrator.py (add)
async def test_low_confidence_reaches_needs_angle_state():
    fake_result = Identification(
        object_type="bottle", brand="Minimalist", product="Serum", variant=None,
        category="Beauty & Personal Care/Skin Care", search_terms=["serum"],
        confidence=0.4, reasoning="blurry", missing_info="concentration",
        suggested_photo="front label",
    )
    with patch("app.orchestrator.download_media", AsyncMock(return_value=b"bytes")), \
         patch("app.orchestrator.archive_photo", return_value="path.jpg"), \
         patch("app.orchestrator.identify", AsyncMock(return_value=fake_result)), \
         patch("app.orchestrator.compose_and_send", AsyncMock()) as mock_compose:
        await handle_photo_message("+91XXXXXXXXXX", "chat1", "https://x/y.jpg")
    sent_state = mock_compose.call_args.args[1]
    assert sent_state.value == "NEEDS_ANGLE"
```

Run: `pytest tests/test_orchestrator.py -v`
Expected: all PASS, including the new one.

- [ ] **Step 3: Commit**

```bash
git add app/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: route identification through confidence gate and state machine"
```

---

## Phase Gate — must pass before Phase 3 starts

- [ ] `pytest` passes for `tests/test_state_machine.py`, `tests/test_reply_composer.py`, `tests/test_orchestrator.py`.
- [ ] **Live test 1 (NEEDS_ANGLE):** text a deliberately ambiguous/blurry photo (or partially obscured label) of the demo object. Confirm the reply asks for a *specific* angle (e.g. "photo of the front label, straight-on"), not a generic "try again." Confirm `items.state = 'NEEDS_ANGLE'` in Supabase.
- [ ] **Live test 2 (UNBUYABLE):** text a photo of something clearly outside the registry (e.g. a laptop, a book). Confirm a graceful decline naming the item. Confirm `items.state = 'UNBUYABLE'`.
- [ ] **Live test 3 (IDENTIFIED):** text a clean photo of the demo object. Confirm `items.state = 'IDENTIFIED'` and the reply names brand/product/variant.
- [ ] Typing indicator visibly appears in Messages before each reply during the live tests above.
- [ ] Everything committed.
