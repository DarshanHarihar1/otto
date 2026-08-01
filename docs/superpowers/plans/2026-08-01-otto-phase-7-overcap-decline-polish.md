# Otto Phase 7: Over-cap Decline & Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The demo's best 30 seconds — a refill request that exceeds the mandate cap gets refused by the card network, not silently blocked by app logic, and Otto says so on camera. Plus a copy audit pass so every user-facing string matches the spec's scripted examples exactly.

**Architecture:** Extend Phase 5's mandate charge path with a pre-flight cap check before calling Prava (so the "decline" is real and attributable to the card network, matching spec §5.3's framing), and a final pass wrapping every existing `send_text` call in `send_typing`.

**Tech Stack:** No new dependencies.

## Global Constraints

*(Same as Phase 0. This phase directly implements priority-13 from the spec's feature table: "Deliberate over-cap decline, shown on camera" — marked demo-critical.)*

- The decline must read as the **card network** refusing, not Otto refusing — say so explicitly in the message, matching spec §5.3's scripted line.
- Priorities 2, 3, 7, 12 (confidence gate, clarification loop, graceful decline, over-cap decline) are the spec's **never-cut** list — this phase finishes that list.

**Prerequisite:** Phase 6 complete and its gate passed.

---

## File Structure

```
app/
├── mandates.py         # (modified) cap check before charge, raises MandateCapExceeded
├── orchestrator.py        # (modified) catch MandateCapExceeded, send scripted decline copy
└── reply_composer.py        # (modified) audit pass — verify every state's copy against spec §4/§5
tests/
└── test_mandates.py           # (extended) cap-exceeded test
```

---

### Task 1: Cap check and decline handling

**Files:**
- Modify: `app/mandates.py`
- Modify: `app/orchestrator.py`
- Test: `tests/test_mandates.py`

**Interfaces:**
- Produces: `class MandateCapExceeded(Exception)` with attributes `requested_paise: int`, `cap_paise: int`. `charge_mandate` (Phase 5) now raises this before making any Prava call when `amount_paise > cap_paise`. Orchestrator's refill branch catches it.

- [ ] **Step 1: Add the pre-flight check**

The cap itself should come from what Prava actually enforces server-side (the mandate was created with `cap_paise` in Phase 5 Task 2) — this client-side check exists so Otto can compose the decline message without waiting on a slow round-trip that would also fail, and so the message can state the exact numbers.

```python
# app/mandates.py (add above charge_mandate)
class MandateCapExceeded(Exception):
    def __init__(self, requested_paise: int, cap_paise: int):
        self.requested_paise = requested_paise
        self.cap_paise = cap_paise
        super().__init__(f"{requested_paise} exceeds cap {cap_paise}")


async def charge_mandate(mandate_id: str, amount_paise: int, cap_paise: int = DEFAULT_MANDATE_CAP_PAISE) -> PaymentResult:
    if amount_paise > cap_paise:
        raise MandateCapExceeded(amount_paise, cap_paise)
    return await _prava_charge_mandate(mandate_id, amount_paise)
```

Update the one existing caller: `charge_mandate` is still called the same way from `app/orchestrator.py`'s refill branch — no signature change needed there since `cap_paise` defaults.

- [ ] **Step 2: Support quantity in the refill intent, and catch the exception**

```python
# app/orchestrator.py (refill branch in handle_text_message, replace the body)
import re
from app.mandates import MandateCapExceeded

    if text_lower.startswith("refill "):
        rest = text_lower.removeprefix("refill ").strip()
        qty_match = re.search(r"[x×]\s*(\d+)$", rest)
        quantity = int(qty_match.group(1)) if qty_match else 1
        label_query = re.sub(r"[x×]\s*\d+$", "", rest).strip()

        shelf_item = find_shelf_item(user_phone, label_query)
        if shelf_item is None:
            await send_text(chat_id, f"I don't have a saved item matching '{label_query}' yet.")
            return
        if shelf_item.mandate_id is None:
            await send_text(chat_id, "No standing approval for that item yet — I'll need you to approve it again.")
            return

        total_paise = shelf_item.last_price_paise * quantity
        try:
            await charge_mandate(shelf_item.mandate_id, total_paise)
        except MandateCapExceeded as e:
            await send_text(
                chat_id,
                f"That's ₹{e.requested_paise/100:.0f} — over the ₹{e.cap_paise/100:.0f} "
                f"cap you set. The card network declined it. Want to raise the cap?",
            )
            return

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO purchases (item_id, amount_paise, status) VALUES (%s, %s, 'PAID')",
                (shelf_item.item_id, total_paise),
            )
        await send_text(chat_id, f"On its way. ₹{total_paise/100:.0f}, same as last time.")
        return
```

- [ ] **Step 3: Write the cap-exceeded test**

```python
# tests/test_mandates.py (append)
import pytest

from app.mandates import MandateCapExceeded, charge_mandate


async def test_charge_mandate_raises_when_over_cap():
    with pytest.raises(MandateCapExceeded) as exc_info:
        await charge_mandate("fake-mandate-id", amount_paise=1_098_000, cap_paise=100_000)
    assert exc_info.value.requested_paise == 1_098_000
    assert exc_info.value.cap_paise == 100_000
```

Run: `pytest tests/test_mandates.py -v`
Expected: all PASS, including the new one. This one is intentionally mocked-free-by-construction — it never reaches the network because the whole point is the check happens before the Prava call.

- [ ] **Step 4: Commit**

```bash
git add app/mandates.py app/orchestrator.py tests/test_mandates.py
git commit -m "feat: mandate cap pre-flight check and scripted over-cap decline"
```

---

### Task 2: Typing indicators and copy audit

**Files:**
- Modify: `app/reply_composer.py`
- Modify: `app/orchestrator.py`

**Interfaces:**
- No new interfaces — this task is a consistency pass over existing code, not new functionality.

- [ ] **Step 1: Ensure every `send_text` call site is preceded by `send_typing(chat_id, True)` and followed by `send_typing(chat_id, False)`**

Grep every call site and confirm the pattern:

```bash
grep -n "send_text(" app/orchestrator.py app/routes/prava_callback.py
```

Any call site found calling `send_text` directly (bypassing `compose_and_send`) — which by this phase is most of them, since Phases 3–6 added direct `send_text` calls for speed — should be wrapped:

```python
# app/reply_composer.py (add a thin helper, use it to replace direct send_text calls)
from app.routes.webhook import send_text as _raw_send_text


async def send_with_typing(chat_id: str, text: str) -> None:
    await send_typing(chat_id, True)
    await _raw_send_text(chat_id, text)
    await send_typing(chat_id, False)
```

Replace every direct `await send_text(chat_id, ...)` call added in Phases 3–6 with `await send_with_typing(chat_id, ...)`, and update the corresponding `from app.routes.webhook import send_text` imports in `app/orchestrator.py` and `app/routes/prava_callback.py` to `from app.reply_composer import send_with_typing as send_text` so no call-site text changes are needed — only the import line changes.

- [ ] **Step 2: Copy audit — diff every user-facing string against the spec**

Check each against `otto-architecture.md` §4/§5 verbatim examples:

| State | Spec's example copy | Check |
|---|---|---|
| `NEEDS_ANGLE` | *"Fairly sure that's Minimalist salicylic serum, but I can't read %. Photo front label?"* | `app/reply_composer.py` |
| `UNBUYABLE` | *"That's MacBook Pro 14". I can't buy that — Apple doesn't sell through checkout I use."* | `app/reply_composer.py` |
| `SUBSTITUTE_OFFERED` | *"Can't get Dove one. Minimalist close match — ₹399, 50ml, also fragrance-free. Different brand. Want it?"* | `app/orchestrator.py` (Phase 6 Task 2) |
| Order confirmation | *"Ordered ✅ · saved shelf 'serum'"* | `app/routes/prava_callback.py` (Phase 4 Task 3) |
| Refill confirmation | *"On its way. ₹549, same last time."* | `app/orchestrator.py` (Phase 5/7) |
| Over-cap decline | *"That's ₹10,980 — over ₹1,000 cap you set. card network declined it. Want raise cap?"* | `app/orchestrator.py` (Task 1 above) |

Fix any wording drift found. This is a manual read-through, not a scripted step — there is no test for prose tone.

- [ ] **Step 3: Commit**

```bash
git add app/reply_composer.py app/orchestrator.py app/routes/prava_callback.py
git commit -m "polish: typing indicators on every reply, copy audit against spec examples"
```

---

## Phase Gate — must pass before recording the demo (spec §8, Hours 17–20)

- [ ] `pytest` passes for the full suite: `pytest -v` with no failures across all phases.
- [ ] **Live test (over-cap decline):** on the demo device, text "refill serum x20" (or a quantity that clearly exceeds the ₹1,000 default cap). Confirm the reply states the requested total, the cap, and explicitly attributes the decline to "the card network" — not to Otto refusing. Confirm no charge appears on the Prava dashboard for this attempt.
- [ ] **Live test (typing indicators):** replay all four endings (buy, substitute, decline, unbuyable) and confirm the typing indicator is visibly present before every single bot reply in the Messages app.
- [ ] **Live full run-through, back to back, on the actual demo device:** identification → clarification (`NEEDS_ANGLE`) → purchase → refill → substitution → over-cap decline, in one sitting, no restarts. This is the actual demo video content — if it doesn't run clean back-to-back here, it won't on camera either.
- [ ] Remaining Prava sandbox test-card transactions for the day are enough for the actual recording take(s) — check the 30/day cap noted in Phase 4 before doing a full dry run more than once or twice.
- [ ] Everything committed. This is the last engineering phase — from here, spec §8's Hours 17–20 (record demo, write submission, publish) are manual, non-code work outside this plan series.
