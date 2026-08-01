# Otto Phase 0: Foundation & Scaffolding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the repo, DB schema, verified merchant registry, and a working Linq echo bot reachable from a real phone over a public tunnel — the spine every later phase builds on.

**Architecture:** Single FastAPI service, `BackgroundTasks` for async work (no Redis/Celery), Supabase Postgres for state, Cloudflare Tunnel (or ngrok) exposing the local server to Linq's webhook.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, httpx, openai, linq-python, python-dotenv, psycopg[binary], Supabase (Postgres + Storage free tier).

## Global Constraints

*(Applies to every phase in this plan series — copied verbatim from `otto-architecture.md`.)*

- Python 3.11+, FastAPI, `httpx`, `openai`, `linq-python`, `python-dotenv`, `psycopg[binary]` — no other frameworks.
- No Redis, no Celery, no queue system — FastAPI `BackgroundTasks` only.
- No memory framework (Mem0/Zep/supermemory) — structured Postgres tables only (`users`, `items`, `purchases`, `events`).
- The amount sent to Prava must come from a live Shopify JSON response, **never from model output**.
- Brand hard filter: confident brand match → route to that single store only. Fan-out only fires when brand is unknown.
- Never substitute in health/pharmacy categories (`NO_SUBSTITUTE_CATEGORIES`).
- Substitution: one offer only. No second suggestion after a "no".
- Linq rejects links in the first outbound message of a new chat — price message and approval link must always be two separate `POST` sends.
- Run the service on the laptop behind Cloudflare Tunnel/ngrok. Do **not** deploy to Render free tier as the primary path (cold start reads as broken on camera). Optional Render deploy is a fallback link only.
- One category, one demo object, one hardcoded user. No auth, no onboarding UI, no multi-tenant anything.
- All model outputs that gate spend or state transitions must be structured outputs (Responses API `response_format`/schema) — never parse free text into a purchase decision.
- Deadline: Aug 2, 3:00 PM PT (confirm in Discord — handbook is inconsistent). Every phase gate below is a real go/no-go against that clock.

### ⚠️ Parallel manual action — not a coded task, do this in the first hour regardless

Per spec §7, email `support@prava.space` (or post in the [Prava Discord](https://discord.gg/j6NzpSmuJ)) requesting temporary production access, in parallel with Task 1 below. This has no code and no test — it's a human-reviewed request with a multi-hour turnaround, so the earlier it's sent the more likely it lands before the demo. If it's granted, a real `prava` CLI end-to-end purchase becomes an optional hero moment layered on top of the sandbox build in Phase 4 — the sandbox path in this plan series works regardless of the outcome, so do not block on a reply.

---

## File Structure

```
otto/
├── .env.example
├── .gitignore
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, router mounting
│   ├── config.py             # env-driven settings
│   ├── db.py                  # psycopg connection helper
│   └── routes/
│       ├── __init__.py
│       └── webhook.py         # Linq inbound webhook receiver
├── migrations/
│   └── 001_init.sql           # users, items, purchases, events
├── scripts/
│   └── probe_merchants.py     # merchant registry verifier
├── data/
│   ├── merchants_source.csv   # manual export of the 50-merchant sheet (Domain, Category)
│   └── merchants.csv          # generated: verified survivors only
└── tests/
    ├── conftest.py
    └── test_webhook.py
```

This phase touches nothing beyond scaffolding — every later phase adds files to `app/`, never restructures this layout.

---

### Task 1: Repo init, dependencies, config

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Test: `tests/conftest.py`

**Interfaces:**
- Produces: `app.config.settings` — a module-level object with attributes `linq_api_token: str`, `linq_webhook_secret: str`, `openai_api_key: str`, `prava_api_key: str`, `prava_base_url: str`, `supabase_db_url: str`, `demo_user_phone: str`. Every later phase imports `from app.config import settings`.

- [ ] **Step 1: Initialize git and Python project**

```bash
cd /Users/darshanharihar/Documents/otto
git init
```

Create `.gitignore`:

```
.env
__pycache__/
*.pyc
.venv/
data/merchants.csv
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "otto"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "httpx>=0.27",
    "openai>=1.54",
    "linq-python>=0.1",
    "python-dotenv>=1.0",
    "psycopg[binary]>=3.2",
    "pydantic>=2.9",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "respx>=0.21"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Run: `pip install -e ".[dev]"`
Expected: install succeeds, `python -c "import fastapi, httpx, openai, linq_python, psycopg"` exits 0.

- [ ] **Step 3: Write `.env.example` and `app/config.py`**

`.env.example`:

```bash
LINQ_API_TOKEN=
LINQ_WEBHOOK_SECRET=
OPENAI_API_KEY=
PRAVA_API_KEY=sk_test_
PRAVA_BASE_URL=https://sandbox.api.prava.space
SUPABASE_DB_URL=postgresql://user:pass@host:5432/postgres
DEMO_USER_PHONE=+91XXXXXXXXXX
CONFIDENCE_THRESHOLD=0.80
```

`app/config.py`:

```python
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    linq_api_token: str
    linq_webhook_secret: str
    openai_api_key: str
    prava_api_key: str
    prava_base_url: str
    supabase_db_url: str
    demo_user_phone: str
    confidence_threshold: float


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"missing required env var {key}")
    return value


settings = Settings(
    linq_api_token=_require("LINQ_API_TOKEN"),
    linq_webhook_secret=_require("LINQ_WEBHOOK_SECRET"),
    openai_api_key=_require("OPENAI_API_KEY"),
    prava_api_key=_require("PRAVA_API_KEY"),
    prava_base_url=os.environ.get("PRAVA_BASE_URL", "https://sandbox.api.prava.space"),
    supabase_db_url=_require("SUPABASE_DB_URL"),
    demo_user_phone=_require("DEMO_USER_PHONE"),
    confidence_threshold=float(os.environ.get("CONFIDENCE_THRESHOLD", "0.80")),
)
```

- [ ] **Step 4: Copy `.env.example` to `.env` and fill real values, verify import**

```bash
cp .env.example .env
# fill in real LINQ_API_TOKEN, OPENAI_API_KEY, PRAVA_API_KEY, SUPABASE_DB_URL, DEMO_USER_PHONE by hand
python -c "from app.config import settings; print(settings.demo_user_phone)"
```

Expected: prints your phone number, no `RuntimeError`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .env.example .gitignore app/__init__.py app/config.py
git commit -m "chore: project scaffolding and config"
```

---

### Task 2: Supabase schema — four tables

**Files:**
- Create: `migrations/001_init.sql`
- Create: `app/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `settings.supabase_db_url` (Task 1).
- Produces: `app.db.get_conn()` — returns a `psycopg.Connection` (autocommit). Every later phase that touches the DB calls `from app.db import get_conn`.

- [ ] **Step 1: Write the migration**

```sql
-- migrations/001_init.sql
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone TEXT UNIQUE NOT NULL,
    prava_customer_id TEXT,
    address_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    label TEXT,
    brand TEXT,
    product TEXT,
    variant TEXT,
    category TEXT,
    merchant TEXT,
    shopify_variant_id TEXT,
    last_price_paise BIGINT,
    mandate_id TEXT,
    state TEXT NOT NULL DEFAULT 'RECEIVED',
    confidence REAL,
    photo_storage_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS purchases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES items(id),
    prava_session_id TEXT,
    amount_paise BIGINT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    item_id UUID REFERENCES items(id),
    kind TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_items_user_state ON items(user_id, state);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(item_id);
```

- [ ] **Step 2: Apply migration and write `app/db.py`**

```bash
psql "$SUPABASE_DB_URL" -f migrations/001_init.sql
```

`app/db.py`:

```python
import psycopg
from app.config import settings


def get_conn() -> psycopg.Connection:
    conn = psycopg.connect(settings.supabase_db_url, autocommit=True)
    return conn
```

- [ ] **Step 3: Write and run the connectivity test**

```python
# tests/test_db.py
from app.db import get_conn


def test_can_query_users_table():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM users")
            row = cur.fetchone()
    assert row[0] >= 0
```

Run: `pytest tests/test_db.py -v`
Expected: PASS — proves the migration applied and credentials work against the real Supabase instance (this is a live integration check, not a mock).

- [ ] **Step 4: Insert the one demo user**

```bash
python -c "
from app.db import get_conn
from app.config import settings
with get_conn() as conn, conn.cursor() as cur:
    cur.execute(
        'INSERT INTO users (phone) VALUES (%s) ON CONFLICT (phone) DO NOTHING',
        (settings.demo_user_phone,),
    )
"
```

- [ ] **Step 5: Commit**

```bash
git add migrations/001_init.sql app/db.py tests/test_db.py
git commit -m "feat: supabase schema and db connection"
```

---

### Task 3: Merchant probe script → verified registry

**Files:**
- Create: `scripts/probe_merchants.py`
- Create: `data/merchants_source.csv` (manual export, see Step 1)
- Test: none (this is a one-shot data-generation script; correctness is verified by its own printed survivor count)

**Interfaces:**
- Produces: `data/merchants.csv` with columns `domain,category,ok` — consumed by Phase 3's registry loader (`app.registry.load_registry`).

- [ ] **Step 1: Export the merchant sheet to CSV**

Open the [50-merchant sheet](https://docs.google.com/spreadsheets/d/1Vwqybz1P9pNz3aQXc8Q4uVqa1p7vYTu_y3ySC7Xsunw/edit?gid=890707389#gid=890707389), File → Download → CSV. Save as `data/merchants_source.csv` with at least `Domain` and `Category` columns. This is a manual step — the sheet is not fetchable by API without OAuth.

- [ ] **Step 2: Write the probe script**

```python
# scripts/probe_merchants.py
import asyncio
import csv
import sys

import httpx


async def probe(client: httpx.AsyncClient, domain: str) -> dict:
    for host in {domain, f"{domain.split('.')[0]}.myshopify.com"}:
        try:
            r = await client.get(
                f"https://{host}/search/suggest.json",
                params={"q": "serum", "resources[type]": "product"},
                timeout=8,
                follow_redirects=True,
            )
            if r.status_code == 200 and "resources" in r.text:
                return {"domain": host, "category": "", "ok": True, "status": 200}
        except Exception as e:
            last = type(e).__name__
        else:
            last = r.status_code
    return {"domain": domain, "category": "", "ok": False, "status": last}


async def main(rows: list[dict]) -> list[dict]:
    async with httpx.AsyncClient(headers={"User-Agent": "otto/1.0"}) as client:
        results = await asyncio.gather(*[probe(client, r["Domain"]) for r in rows])
    for result, row in zip(results, rows):
        result["category"] = row.get("Category", "")
    verified = [r for r in results if r["ok"]]
    print(f"{len(verified)}/{len(results)} merchants respond", file=sys.stderr)
    return verified


if __name__ == "__main__":
    with open("data/merchants_source.csv") as f:
        rows = list(csv.DictReader(f))
    verified = asyncio.run(main(rows))
    with open("data/merchants.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["domain", "category", "ok"])
        writer.writeheader()
        for r in verified:
            writer.writerow({"domain": r["domain"], "category": r["category"], "ok": r["ok"]})
```

- [ ] **Step 3: Run it against the real 50-store list**

```bash
python scripts/probe_merchants.py
```

Expected: stderr prints `N/50 merchants respond` with `N` reasonably high (doc expects most of them); `data/merchants.csv` exists and is non-empty. This is the live integration check for this task — it hits 50 real Shopify stores over the network.

- [ ] **Step 4: Commit**

```bash
git add scripts/probe_merchants.py data/merchants_source.csv
git commit -m "feat: merchant probe script and source list"
```

(`data/merchants.csv` stays untracked per `.gitignore` — it's generated output, re-run before demo day in case a store goes down.)

---

### Task 4: FastAPI skeleton + Linq webhook echo bot

**Files:**
- Create: `app/main.py`
- Create: `app/routes/__init__.py`
- Create: `app/routes/webhook.py`
- Test: `tests/test_webhook.py`

**Interfaces:**
- Consumes: `settings.linq_api_token`, `settings.linq_webhook_secret` (Task 1).
- Produces: `POST /webhook/linq` route mounted on the app. `app.routes.webhook.send_text(chat_id: str, text: str) -> None` — every later phase's reply composer builds on this.

- [ ] **Step 1: Write the webhook receiver with Standard Webhooks signature verification**

Linq signs webhooks using the [Standard Webhooks](https://www.standardwebhooks.com/) format, not a simple HMAC-over-body: three headers (`webhook-id`, `webhook-timestamp`, `webhook-signature`), a signed string of `"{id}.{timestamp}.{body}"`, a key derived by stripping the `whsec_` prefix from the secret and base64-decoding the remainder, and replay protection (reject anything older than 5 minutes). There's a legacy `X-Webhook-Signature` header too, but build against the current one.

```python
# app/routes/webhook.py
import base64
import hmac
import hashlib
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.config import settings

router = APIRouter()

LINQ_BASE = "https://api.linqapp.com/api/partner/v3"


def _verify_signature(
    body: bytes, msg_id: str | None, timestamp: str | None, signature: str | None
) -> bool:
    if not (msg_id and timestamp and signature):
        return False
    if abs(time.time() - int(timestamp)) > 300:
        return False  # replay protection: reject anything older than 5 minutes

    secret = settings.linq_webhook_secret.removeprefix("whsec_")
    key = base64.b64decode(secret)
    signed_content = f"{msg_id}.{timestamp}.{body.decode()}"
    expected = base64.b64encode(
        hmac.new(key, signed_content.encode(), hashlib.sha256).digest()
    ).decode()

    for sig in signature.split(" "):
        if sig.startswith("v1,") and hmac.compare_digest(expected, sig[3:]):
            return True
    return False


async def send_text(chat_id: str, text: str) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{LINQ_BASE}/chats/{chat_id}/messages",
            headers={"Authorization": f"Bearer {settings.linq_api_token}"},
            json={"text": text},
            timeout=15,
        )


def _handle_message_received(payload: dict) -> None:
    chat_id = payload["data"]["chat_id"]
    text = payload["data"].get("text", "")
    import asyncio

    asyncio.run(send_text(chat_id, f"got it: {text}" if text else "got your message"))


@router.post("/webhook/linq")
async def linq_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    webhook_id: str | None = Header(default=None),
    webhook_timestamp: str | None = Header(default=None),
    webhook_signature: str | None = Header(default=None),
):
    body = await request.body()
    if not _verify_signature(body, webhook_id, webhook_timestamp, webhook_signature):
        raise HTTPException(status_code=401, detail="bad signature")
    payload = await request.json()
    if payload.get("event") == "message.received":
        background_tasks.add_task(_handle_message_received, payload)
    return {"ok": True}
```

- [ ] **Step 2: Wire it into the app**

```python
# app/main.py
from fastapi import FastAPI

from app.routes.webhook import router as webhook_router

app = FastAPI(title="otto")
app.include_router(webhook_router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 3: Write the signature-verification unit test**

```python
# tests/test_webhook.py
import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)


def _sign(body: bytes, msg_id: str, timestamp: str) -> str:
    secret = settings.linq_webhook_secret.removeprefix("whsec_")
    key = base64.b64decode(secret)
    signed_content = f"{msg_id}.{timestamp}.{body.decode()}"
    digest = base64.b64encode(
        hmac.new(key, signed_content.encode(), hashlib.sha256).digest()
    ).decode()
    return f"v1,{digest}"


def test_rejects_bad_signature():
    body = json.dumps({"event": "message.received", "data": {}}).encode()
    resp = client.post(
        "/webhook/linq",
        content=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": str(int(time.time())),
            "webhook-signature": "v1,bad",
        },
    )
    assert resp.status_code == 401


def test_accepts_valid_signature():
    body = json.dumps(
        {"event": "message.received", "data": {"chat_id": "c1", "text": "hi"}}
    ).encode()
    timestamp = str(int(time.time()))
    resp = client.post(
        "/webhook/linq",
        content=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": timestamp,
            "webhook-signature": _sign(body, "msg_1", timestamp),
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_rejects_stale_timestamp():
    body = json.dumps({"event": "message.received", "data": {}}).encode()
    stale_timestamp = str(int(time.time()) - 600)  # 10 minutes old
    resp = client.post(
        "/webhook/linq",
        content=body,
        headers={
            "webhook-id": "msg_1",
            "webhook-timestamp": stale_timestamp,
            "webhook-signature": _sign(body, "msg_1", stale_timestamp),
        },
    )
    assert resp.status_code == 401
```

Note: `settings.linq_webhook_secret` must already be a `whsec_`-prefixed, base64-encoded value in `.env` for these tests to run — that value doesn't exist yet at this point in the task, since it's only issued by the real subscription-creation call in Step 4 below. For this test file to pass standalone before Step 4, put a syntactically valid placeholder in `.env` now (e.g. `LINQ_WEBHOOK_SECRET=whsec_dGVzdHNlY3JldGZvcnVuaXR0ZXN0cw==`) and overwrite it with the real value once Step 4 issues one — the placeholder only needs to be valid base64 after the prefix, not a real Linq secret, since these three tests never call the real Linq API.

Run: `pytest tests/test_webhook.py -v`
Expected: all 3 PASS.

- [ ] **Step 4: Run the server, open the tunnel, and create the real webhook subscription**

```bash
uvicorn app.main:app --reload --port 8000
# in a second terminal:
cloudflared tunnel --url http://localhost:8000
```

Copy the printed `https://*.trycloudflare.com` URL — this changes every time the tunnel restarts (quick tunnels aren't stable), so this step is repeated whenever that happens.

The webhook subscription is created via API, not the dashboard, and its response is the **only** place the real signing secret ever appears:

```bash
curl -X POST "https://api.linqapp.com/api/partner/v3/webhook-subscriptions" \
  -H "Authorization: Bearer $LINQ_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://<your-tunnel-url>/webhook/linq","events":["message.received"]}'
```

Copy the `signing_secret` field from the response immediately into `.env` as `LINQ_WEBHOOK_SECRET` (replacing the Step 3 placeholder) — the docs are explicit that it **cannot be retrieved again**; losing it means deleting and recreating the subscription. Restart `uvicorn` so it picks up the new `.env` value.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routes/__init__.py app/routes/webhook.py tests/test_webhook.py
git commit -m "feat: fastapi skeleton and linq echo webhook"
```

---

## Phase Gate — must pass before Phase 1 starts

This is a **live, end-to-end** check, not just green tests. Do not proceed to Phase 1 until every line below is checked.

- [ ] `pytest` passes for all of `tests/test_db.py` and `tests/test_webhook.py`.
- [ ] `data/merchants.csv` exists with a survivor count printed to stderr — sanity-check it's not suspiciously low (e.g. `<20/50`); if it is, investigate before moving on (Phase 3 depends on this list).
- [ ] Supabase: the `users` table has exactly one row, with `phone = settings.demo_user_phone`.
- [ ] **Live test:** from your actual phone, text the Linq sandbox number anything (e.g. "hello otto"). Confirm an iMessage reply ("got it: hello otto") arrives within a few seconds.
- [ ] `GET https://<tunnel-url>/health` returns `{"status": "ok"}` from a browser, proving the tunnel is live and stable.
- [ ] Everything above is committed to git; `git log --oneline` shows 4 commits for this phase.
