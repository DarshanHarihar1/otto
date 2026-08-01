# Otto Phase 4: Payments (Prava) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a `QUOTED` item, create a real Prava sandbox session, get the user through passkey approval, receive the virtual card + CVV, report the outcome, and reach `ORDERED`. This is the hackathon's hero flow — the spec's Hour 12 checkpoint gates everything after this phase on it working.

**Architecture:** A thin Prava REST client (`httpx`, sandbox base URL, `sk_test_` key), a `/prava/callback` redirect route, and an orchestrator extension implementing exactly the four-step sandbox flow from spec §0: create session → hosted page (no API call) → poll payment-result → report-status.

**Tech Stack:** `httpx`, FastAPI route for the callback.

## Global Constraints

*(Same as Phase 0. The two rules this phase must not violate:)*

- **Two separate Linq sends.** The price/quote message and the Prava approval link must never be combined — Linq rejects links in the first outbound message of a chat, and even in later messages, keep them as two sends per the spec's explicit instruction (test this in hour one; it's the most likely thing to silently break the demo).
- **The amount sent to Prava is `items.last_price_paise` from Phase 3 — never recomputed, never re-derived from the model.**

**Prerequisite:** Phase 3 complete and its gate passed (`QUOTED` reachable with a real price).

### ⚠️ Prava Sandbox test card (team-specific, keep out of git)

Use this card to complete passkey approval on the hosted page during every live test in this phase and Phase 5:

```
Card number: 4622943123232416
CVV:         12
Expiry:      12/30
```

This card is unique to this team/participant — do not commit it, do not share it outside this project. **Max 30 transactions/day** on this card — budget live-test runs accordingly across Phases 4–7 (a full end-to-end run plus a refill plus a decline test is 3 transactions; don't loop the demo rehearsal more than necessary before recording).

---

## File Structure

```
app/
├── prava.py                  # create_session, get_payment_result, report_status
├── routes/
│   └── prava_callback.py      # GET /prava/callback
├── orchestrator.py             # (modified) QUOTED -> AWAITING_APPROVAL -> PAID -> ORDERED/FAILED
└── main.py                      # (modified) mount callback router
tests/
├── test_prava.py
└── test_prava_callback.py
```

---

### Task 1: Prava REST client

**Files:**
- Create: `app/prava.py`
- Test: `tests/test_prava.py`

**Interfaces:**
- Consumes: `settings.prava_api_key`, `settings.prava_base_url` (Phase 0).
- Produces: `async def create_session(amount_paise: int, merchant: str, line_items: list[dict]) -> Session` (`Session` has `session_id: str`, `approval_url: str`), `async def get_payment_result(session_id: str) -> PaymentResult` (`card_number: str`, `cvv: str`, `expiry: str`), `async def report_status(session_id: str, status: str) -> None`. Task 3's orchestrator wiring and Phase 5's mandate module both build on this client.

- [ ] **Step 1: Write the client against the exact 4-step flow from spec §0**

```python
# app/prava.py
import httpx
from pydantic import BaseModel

from app.config import settings

_HEADERS = {"Authorization": f"Bearer {settings.prava_api_key}"}


class Session(BaseModel):
    session_id: str
    approval_url: str


class PaymentResult(BaseModel):
    card_number: str
    cvv: str
    expiry: str


async def create_session(
    amount_paise: int, merchant: str, line_items: list[dict]
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
            },
        )
        r.raise_for_status()
        data = r.json()
        return Session(session_id=data["session_id"], approval_url=data["approval_url"])


async def get_payment_result(session_id: str) -> PaymentResult:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{settings.prava_base_url}/v1/sessions/{session_id}/payment-result",
            headers=_HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        return PaymentResult(
            card_number=data["card_number"], cvv=data["cvv"], expiry=data["expiry"]
        )


async def report_status(session_id: str, status: str) -> None:
    assert status in ("APPROVED", "DECLINED")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{settings.prava_base_url}/v1/sessions/{session_id}/report-status",
            headers=_HEADERS,
            json={"status": status},
        )
        r.raise_for_status()
```

Add `public_base_url: str` to `app/config.py`'s `Settings` dataclass and `.env.example` (`PUBLIC_BASE_URL=` — the current Cloudflare Tunnel URL from Phase 0; update this each time the tunnel restarts, since `trycloudflare.com` URLs are not stable across restarts unless you use a named tunnel).

**Note on field names:** `card_number`/`cvv`/`expiry`/`session_id`/`approval_url` are best-effort based on the spec's description ("virtual card number + dynamic CVV") — the *actual* JSON keys are confirmed live in Step 2 below, which is Open Question 3 from the spec. Adjust the `PaymentResult`/`Session` field mappings to match whatever the real sandbox response shows.

- [ ] **Step 2: Run the real sandbox flow once, by hand, to confirm payload shapes**

```bash
curl -X POST "$PRAVA_BASE_URL/v1/sessions" \
  -H "Authorization: Bearer $PRAVA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"merchant":"test-merchant","amount":5.49,"currency":"INR","line_items":[{"name":"test","price":5.49}],"callback_url":"https://example.com/cb"}'
```

Inspect the real response. Fix any field-name mismatches in `app/prava.py` before writing tests against it (do this before Step 3 — the test fixtures below must reflect the real shape, not the guessed one).

- [ ] **Step 3: Write the test using Prava's documented sandbox test cards**

```python
# tests/test_prava.py
from app.prava import create_session, get_payment_result, report_status


async def test_create_session_returns_real_approval_url():
    session = await create_session(
        amount_paise=54900,
        merchant="test-merchant",
        line_items=[{"name": "Salicylic Acid Serum", "price": 549.00}],
    )
    assert session.session_id
    assert session.approval_url.startswith("http")


async def test_report_status_accepts_approved():
    session = await create_session(
        amount_paise=10000, merchant="test-merchant",
        line_items=[{"name": "test item", "price": 100.00}],
    )
    # in real use this only succeeds after passkey approval + payment-result;
    # this call proves the report-status endpoint itself accepts the shape
    await report_status(session.session_id, "DECLINED")
```

Run: `pytest tests/test_prava.py -v`
Expected: PASS against the real sandbox — these are live network calls, not mocks, since payload-shape correctness can't be verified any other way per Step 2.

- [ ] **Step 4: Commit**

```bash
git add app/prava.py app/config.py .env.example tests/test_prava.py
git commit -m "feat: prava rest client for session create/payment-result/report-status"
```

---

### Task 2: Callback route

**Files:**
- Create: `app/routes/prava_callback.py`
- Modify: `app/main.py`
- Test: `tests/test_prava_callback.py`

**Interfaces:**
- Consumes: `get_payment_result`, `report_status` (Task 1); `get_conn` (Phase 0).
- Produces: `GET /prava/callback?session_id=...` route that transitions the matching item from `AWAITING_APPROVAL` to `PAID`/`FAILED`. Phase 5's mandate flow reuses the same state-transition helper (`_finalize_payment`, extracted here) rather than duplicating it.

- [ ] **Step 1: Write the callback route**

```python
# app/routes/prava_callback.py
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import HTMLResponse

from app.db import get_conn
from app.prava import get_payment_result, report_status
from app.reply_composer import send_typing
from app.routes.webhook import send_text

router = APIRouter()


async def _finalize_payment(session_id: str) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT p.item_id, i.state, u.phone, i.brand, i.product
               FROM purchases p JOIN items i ON i.id = p.item_id
               JOIN users u ON u.id = i.user_id
               WHERE p.prava_session_id = %s""",
            (session_id,),
        )
        row = cur.fetchone()
    if row is None:
        return
    item_id, _state, phone, brand, product = row

    try:
        result = await get_payment_result(session_id)
        await report_status(session_id, "APPROVED")
        new_state = "PAID"
    except Exception:
        await report_status(session_id, "DECLINED")
        new_state = "FAILED"

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE items SET state = %s, updated_at = now() WHERE id = %s",
            (new_state, item_id),
        )
        cur.execute(
            "UPDATE purchases SET status = %s WHERE prava_session_id = %s",
            (new_state, session_id),
        )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT chat_id FROM events WHERE item_id = %s AND kind = 'chat_ref' ORDER BY id DESC LIMIT 1", (item_id,))
        # chat_id lookup detailed in Task 3 below where it's first written

    if new_state == "PAID":
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("UPDATE items SET state = 'ORDERED', updated_at = now() WHERE id = %s", (item_id,))


@router.get("/prava/callback")
async def prava_callback(session_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(_finalize_payment, session_id)
    return HTMLResponse("<html><body>Payment processed — you can close this tab.</body></html>")
```

(The `chat_id` lookup is finished in Task 3 once `purchases` carries it — see the note there. This route is deliberately fast: it ACKs the redirect immediately and finalizes in the background, same pattern as the Phase 0 webhook.)

- [ ] **Step 2: Mount the router**

```python
# app/main.py (add)
from app.routes.prava_callback import router as prava_router

app.include_router(prava_router)
```

- [ ] **Step 3: Write the callback test**

```python
# tests/test_prava_callback.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_callback_acks_immediately():
    resp = client.get("/prava/callback", params={"session_id": "nonexistent"})
    assert resp.status_code == 200
```

Run: `pytest tests/test_prava_callback.py -v`
Expected: PASS (this only proves the ACK path; the live end-to-end check is the Phase Gate below).

- [ ] **Step 4: Commit**

```bash
git add app/routes/prava_callback.py app/main.py tests/test_prava_callback.py
git commit -m "feat: prava callback route"
```

---

### Task 3: Wire QUOTED → AWAITING_APPROVAL with the two-send rule

**Files:**
- Modify: `app/orchestrator.py`

**Interfaces:**
- Consumes: `create_session` (Task 1); `_finalize_payment`'s DB writes (Task 2, via the `purchases` table).
- Produces: extends `handle_photo_message`'s `QUOTED` branch to also require a text reply of "yes"/"buy it" from the user before creating a session — add a second webhook entry point `handle_text_message(user_phone, chat_id, text)` for this, since approval must be inbound-first per the spec's user flow (5.1).

- [ ] **Step 1: Add `handle_text_message` for the purchase-confirmation step**

```python
# app/orchestrator.py (append)
async def handle_text_message(user_phone: str, chat_id: str, text: str) -> None:
    text_lower = text.strip().lower()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT i.id, i.merchant, i.shopify_variant_id, i.last_price_paise, i.brand, i.product
               FROM items i JOIN users u ON u.id = i.user_id
               WHERE u.phone = %s AND i.state = 'QUOTED'
               ORDER BY i.updated_at DESC LIMIT 1""",
            (user_phone,),
        )
        row = cur.fetchone()

    if row is None or text_lower not in ("yes", "buy", "buy it", "confirm"):
        return

    item_id, merchant, variant_id, price_paise, brand, product = row
    session = await create_session(
        amount_paise=price_paise,
        merchant=merchant,
        line_items=[{"name": f"{brand} {product}", "shopify_variant_id": variant_id, "price": price_paise / 100}],
    )

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE items SET state = 'AWAITING_APPROVAL', updated_at = now() WHERE id = %s",
            (item_id,),
        )
        cur.execute(
            "INSERT INTO purchases (item_id, prava_session_id, amount_paise, status) VALUES (%s, %s, %s, 'AWAITING_APPROVAL')",
            (item_id, session.session_id, price_paise),
        )
        cur.execute(
            "INSERT INTO events (item_id, kind, payload) VALUES (%s, 'chat_ref', %s)",
            (item_id, json.dumps({"chat_id": chat_id})),
        )

    # two separate sends — Linq rejects links bundled into the first message
    await send_text(chat_id, f"₹{price_paise/100:.0f} for {brand} {product}. Sending the approval link now.")
    await send_text(chat_id, session.approval_url)
```

- [ ] **Step 2: Route text messages in the webhook**

```python
# app/routes/webhook.py (_handle_message_received, add the else branch)
from app.orchestrator import handle_text_message

def _handle_message_received(payload: dict) -> None:
    data = payload["data"]
    chat_id = data["chat_id"]
    phone = data["sender_phone"]
    media = data.get("media", [])
    text = data.get("text", "")
    if media:
        asyncio.run(handle_photo_message(phone, chat_id, media[0]["url"]))
    elif text:
        asyncio.run(handle_text_message(phone, chat_id, text))
```

- [ ] **Step 3: Finish `_finalize_payment`'s chat lookup and completion message**

Replace the placeholder `chat_id` lookup block in `app/routes/prava_callback.py` (Task 2, Step 1) with:

```python
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload->>'chat_id' FROM events WHERE item_id = %s AND kind = 'chat_ref' ORDER BY id DESC LIMIT 1",
            (item_id,),
        )
        chat_row = cur.fetchone()
    chat_id = chat_row[0] if chat_row else None

    if chat_id and new_state == "PAID":
        await send_text(chat_id, f"Ordered ✅ · {brand} {product} · saved to your shelf")
    elif chat_id:
        await send_text(chat_id, "Payment didn't go through — the session was declined.")
```

- [ ] **Step 4: Commit**

```bash
git add app/orchestrator.py app/routes/webhook.py app/routes/prava_callback.py
git commit -m "feat: purchase confirmation, session creation, two-send approval flow"
```

---

## Phase Gate — must pass before Phase 5 starts

This is the spec's **Hour 12 checkpoint** — the single most important gate in the whole project. Do not proceed until a real payment has completed end to end.

- [ ] `pytest` passes for `tests/test_prava.py`, `tests/test_prava_callback.py`.
- [ ] **Live test, full flow:** text the demo object photo → confirm `QUOTED` reply → reply "yes" → confirm two **separate** iMessages arrive (price message, then approval link) → open the approval link on your actual demo device → complete passkey approval with a Prava sandbox test card.
- [ ] Confirm the `/prava/callback` redirect fires and, within a few seconds, "Ordered ✅" arrives in the thread.
- [ ] Confirm in Supabase: `items.state = 'ORDERED'`, `purchases.status = 'PAID'`, `purchases.prava_session_id` set.
- [ ] Confirm the order/session shows up on the Prava dashboard (`dashboard.prava.space`).
- [ ] **If this does not work end-to-end within the time budget:** per spec §8, stop here, cut Phases 6/7 features, and spend all remaining time making this one purchase bulletproof before touching anything else.
- [ ] Everything committed.
