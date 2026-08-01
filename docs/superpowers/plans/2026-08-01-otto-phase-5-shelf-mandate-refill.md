# Otto Phase 5: Shelf & Mandate Refill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a first real purchase, texting "refill serum" reorders the same item with no photo and no passkey — the spec's "second act," the moment that makes Otto feel like a product instead of a one-shot demo.

**Architecture:** A mandate is set up during the first purchase's Prava session (Phase 4); this phase adds shelf lookup by label and a mandate-charge path that skips straight to a Prava mandate charge, bypassing `AWAITING_APPROVAL` entirely.

**Tech Stack:** Extends `app/prava.py` (Phase 4) with mandate endpoints; reuses `app/orchestrator.py`'s text-message routing (Phase 4 Task 3).

## Global Constraints

*(Same as Phase 0. For this phase specifically:)*

- The refill flow must be **two messages, no photo, no approval link** — that contrast with the first-purchase flow is explicitly called out as more demo-worthy than the purchase itself.
- Still no memory framework — "shelf" is a plain query over the `items` table by `user_id` + fuzzy label match, not vector search.

**Prerequisite:** Phase 4 complete and its gate passed (one real `ORDERED` purchase exists in the DB).

---

## File Structure

```
app/
├── shelf.py            # label -> most recent purchased item lookup
├── mandates.py           # mandate setup (on first purchase) + charge (on refill)
├── prava.py                # (modified) add mandate endpoints
└── orchestrator.py           # (modified) mandate_setup on first purchase, "refill X" intent
tests/
├── test_shelf.py
└── test_mandates.py
```

---

### Task 1: Shelf lookup

**Files:**
- Create: `app/shelf.py`
- Test: `tests/test_shelf.py`

**Interfaces:**
- Consumes: `get_conn` (Phase 0).
- Produces: `def find_shelf_item(user_phone: str, label_query: str) -> ShelfItem | None` where `ShelfItem` has `item_id: str`, `brand: str`, `product: str`, `merchant: str`, `shopify_variant_id: str`, `last_price_paise: int`, `mandate_id: str | None`. Task 3 of this phase consumes this directly.

- [ ] **Step 1: Write the lookup — simplest possible match, per spec's "structured registry, not semantic recall" stance**

```python
# app/shelf.py
from dataclasses import dataclass

from app.db import get_conn


@dataclass
class ShelfItem:
    item_id: str
    brand: str
    product: str
    merchant: str
    shopify_variant_id: str
    last_price_paise: int
    mandate_id: str | None


def find_shelf_item(user_phone: str, label_query: str) -> ShelfItem | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.id, i.brand, i.product, i.merchant, i.shopify_variant_id,
                      i.last_price_paise, i.mandate_id
               FROM items i JOIN users u ON u.id = i.user_id
               WHERE u.phone = %s AND i.state = 'ORDERED'
                 AND (lower(i.product) LIKE %s OR lower(i.brand) LIKE %s OR lower(i.label) LIKE %s)
               ORDER BY i.updated_at DESC LIMIT 1""",
            (user_phone, f"%{label_query.lower()}%", f"%{label_query.lower()}%", f"%{label_query.lower()}%"),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return ShelfItem(
        item_id=row[0], brand=row[1], product=row[2], merchant=row[3],
        shopify_variant_id=row[4], last_price_paise=row[5], mandate_id=row[6],
    )
```

- [ ] **Step 2: Backfill `items.label` on order completion**

In `app/routes/prava_callback.py`'s `_finalize_payment` (Phase 4 Task 2), add to the `PAID`-branch UPDATE: `label = %s` populated from `f"{brand} {product}"`, so the shelf query above has something predictable to match against beyond brand/product columns alone. This is a one-line addition to the existing `UPDATE items SET state = 'ORDERED', ...` — add `label = %(label)s` there.

- [ ] **Step 3: Write the test against the real purchase from Phase 4's gate**

```python
# tests/test_shelf.py
from app.config import settings
from app.shelf import find_shelf_item


def test_find_shelf_item_matches_real_ordered_item():
    result = find_shelf_item(settings.demo_user_phone, "serum")
    assert result is not None
    assert result.item_id
    assert result.last_price_paise > 0
```

Run: `pytest tests/test_shelf.py -v`
Expected: PASS against the real `ORDERED` row created in Phase 4's live test — this is a live DB check, not a mock.

- [ ] **Step 4: Commit**

```bash
git add app/shelf.py app/routes/prava_callback.py tests/test_shelf.py
git commit -m "feat: shelf lookup by fuzzy label match"
```

---

### Task 2: Mandate setup + charge

**Files:**
- Create: `app/mandates.py`
- Modify: `app/prava.py`
- Test: `tests/test_mandates.py`

**Interfaces:**
- Consumes: `settings.prava_api_key`/`prava_base_url` (Phase 0).
- Produces: `async def create_session_with_mandate(amount_paise: int, merchant: str, line_items: list[dict], cap_paise: int) -> Session` (mandate variant of Phase 4's `create_session`) and `async def charge_mandate(mandate_id: str, amount_paise: int) -> PaymentResult`. Task 3 wires both into the orchestrator; Phase 7 extends `charge_mandate`'s caller with cap-decline handling.

- [ ] **Step 1: Add mandate endpoints to the Prava client**

```python
# app/prava.py (append)
async def create_session_with_mandate(
    amount_paise: int, merchant: str, line_items: list[dict], cap_paise: int
) -> Session:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{settings.prava_base_url}/v1/sessions",
            headers=_HEADERS,
            json={
                "merchant": merchant,
                "amount": amount_paise / 100,
                "currency": "INR",
                "line_items": line_items,
                "callback_url": f"{settings.public_base_url}/prava/callback",
                "mandate_setup": {"cap": cap_paise / 100, "merchant_scoped": True},
            },
        )
        r.raise_for_status()
        data = r.json()
        return Session(session_id=data["session_id"], approval_url=data["approval_url"])


async def get_mandate_id_for_session(session_id: str) -> str | None:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{settings.prava_base_url}/v1/sessions/{session_id}/payment-result",
            headers=_HEADERS,
        )
        r.raise_for_status()
        return r.json().get("mandate_id")


async def charge_mandate(mandate_id: str, amount_paise: int) -> PaymentResult:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{settings.prava_base_url}/mandates/{mandate_id}/charge",
            headers=_HEADERS,
            json={"amount": amount_paise / 100, "currency": "INR"},
        )
        r.raise_for_status()
        data = r.json()
        return PaymentResult(
            card_number=data.get("card_number", ""),
            cvv=data.get("cvv", ""),
            expiry=data.get("expiry", ""),
        )
```

**Note:** exact field names (`mandate_setup`, `mandate_id`, `/mandates/{id}/charge` request body) are best-effort from the spec's description — confirm against `docs.prava.space/api-reference/mandate-charge` and `mandate-lifecycle` and adjust before Step 3's live test, same caveat as Phase 4 Task 1.

- [ ] **Step 2: Write `app/mandates.py` as a thin orchestration wrapper**

```python
# app/mandates.py
from app.prava import charge_mandate as _prava_charge_mandate
from app.prava import PaymentResult

DEFAULT_MANDATE_CAP_PAISE = 100_000  # ₹1,000, per spec's demo scenario


async def charge_mandate(mandate_id: str, amount_paise: int) -> PaymentResult:
    return await _prava_charge_mandate(mandate_id, amount_paise)
```

- [ ] **Step 3: Write the live test**

```python
# tests/test_mandates.py
from app.prava import create_session_with_mandate


async def test_create_session_with_mandate_returns_approval_url():
    session = await create_session_with_mandate(
        amount_paise=54900, merchant="test-merchant",
        line_items=[{"name": "test item", "price": 549.00}],
        cap_paise=100_000,
    )
    assert session.session_id
    assert session.approval_url.startswith("http")
```

Run: `pytest tests/test_mandates.py -v`
Expected: PASS against the real sandbox.

- [ ] **Step 4: Commit**

```bash
git add app/prava.py app/mandates.py tests/test_mandates.py
git commit -m "feat: prava mandate setup and charge"
```

---

### Task 3: Wire it all — first purchase sets up a mandate, "refill X" charges it

**Files:**
- Modify: `app/orchestrator.py`

**Interfaces:**
- Consumes: `find_shelf_item` (Task 1), `charge_mandate` (Task 2), `DEFAULT_MANDATE_CAP_PAISE` (Task 2).
- Produces: `handle_text_message` now branches on a `refill` prefix before falling through to the existing "yes"/QUOTED-confirmation path.

- [ ] **Step 1: Switch first-purchase session creation to the mandate variant**

In `app/orchestrator.py`'s `handle_text_message` (Phase 4 Task 3), replace the `create_session(...)` call with:

```python
from app.mandates import DEFAULT_MANDATE_CAP_PAISE
from app.prava import create_session_with_mandate, get_mandate_id_for_session

    session = await create_session_with_mandate(
        amount_paise=price_paise,
        merchant=merchant,
        line_items=[{"name": f"{brand} {product}", "shopify_variant_id": variant_id, "price": price_paise / 100}],
        cap_paise=DEFAULT_MANDATE_CAP_PAISE,
    )
```

And in `app/routes/prava_callback.py`'s `_finalize_payment`, on the `PAID` branch, add a mandate_id backfill:

```python
        mandate_id = await get_mandate_id_for_session(session_id)
        if mandate_id:
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("UPDATE items SET mandate_id = %s WHERE id = %s", (mandate_id, item_id))
```

- [ ] **Step 2: Add the refill branch to `handle_text_message`**

```python
# app/orchestrator.py (handle_text_message, add before the existing QUOTED-confirmation logic)
from app.shelf import find_shelf_item
from app.mandates import charge_mandate

async def handle_text_message(user_phone: str, chat_id: str, text: str) -> None:
    text_lower = text.strip().lower()

    if text_lower.startswith("refill "):
        label_query = text_lower.removeprefix("refill ").strip()
        shelf_item = find_shelf_item(user_phone, label_query)
        if shelf_item is None:
            await send_text(chat_id, f"I don't have a saved item matching '{label_query}' yet.")
            return
        if shelf_item.mandate_id is None:
            await send_text(chat_id, "No standing approval for that item yet — I'll need you to approve it again.")
            return
        await charge_mandate(shelf_item.mandate_id, shelf_item.last_price_paise)
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO purchases (item_id, amount_paise, status) VALUES (%s, %s, 'PAID')",
                (shelf_item.item_id, shelf_item.last_price_paise),
            )
        await send_text(
            chat_id,
            f"On its way. ₹{shelf_item.last_price_paise/100:.0f}, same as last time.",
        )
        return

    # ... existing QUOTED-confirmation branch below, unchanged
```

- [ ] **Step 3: Commit**

```bash
git add app/orchestrator.py app/routes/prava_callback.py
git commit -m "feat: mandate-based refill flow, no photo no passkey"
```

---

## Phase Gate — must pass before Phase 6 starts

- [ ] `pytest` passes for `tests/test_shelf.py`, `tests/test_mandates.py`.
- [ ] **Live test:** on the demo device, text "refill serum" (or whatever label matches your Phase 4 purchase).
- [ ] Confirm **no photo prompt, no approval link, no passkey step** — just two messages total (the "refill serum" text in, the confirmation text out).
- [ ] Confirm "On its way. ₹549, same as last time." (or equivalent real price) arrives within a few seconds.
- [ ] Confirm a new `purchases` row exists for the same `item_id` with `status = 'PAID'`.
- [ ] Confirm the charge appears on the Prava dashboard as a mandate charge, distinct from the original session-based purchase.
- [ ] Everything committed.
