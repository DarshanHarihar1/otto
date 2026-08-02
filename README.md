# Otto

Photograph a product in iMessage. Otto identifies it, finds a live Shopify price, and lets you buy it in one tap via Prava.

## What it does

1. **Photo in** — Linq webhook delivers an iMessage image
2. **Identify** — OpenAI vision names brand, product, size, and category
3. **Resolve** — Search Shopify merchants (brand store first, then category fan-out)
4. **Quote** — Text back the live price
5. **Checkout** — Tapback or “yes” opens a Prava approval link (sandbox passkey)
6. **Shelf** — Paid items can refill later on mandate

## Features

- **Shop-around** — After quoting, Otto checks 1–2 other category stores for the *same brand* SKU. If it’s ≥₹10 cheaper, it texts `same thing's ₹X cheaper at {store} — say switch?`. Reply `switch` / `cheaper` to buy the alt; `yes` / 👍 keeps the primary quote.
- **Tapback checkout** — Like, love, or emphasize on the quote confirms like typing “yes”; dislike declines.
- **Brand substitution** — If the exact product isn’t found, Otto can offer a labelled substitute in the same category (blocked in health categories). Consent-gated before checkout.
- **Mandate refills** — After a first purchase, Otto can charge an approved mandate for “same again,” with a spend-cap pre-flight so over-cap attempts get a clear decline + fresh approval path.
- **Confidence gate** — Low-confidence vision IDs ask for a clearer photo instead of guessing a wrong SKU.

## Stack

- **Python 3.11+** / FastAPI / uvicorn
- **Postgres** (Supabase)
- **Linq** — iMessage channel
- **OpenAI** — vision + product matching
- **Shopify** — `/search/suggest.json` + product JSON (no storefront API key)
- **Prava** — hosted approval + virtual card (sandbox)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in secrets
```

Apply schema once:

```bash
psql "$SUPABASE_DB_URL" -f migrations/001_init.sql
```

Optional: refresh verified merchants:

```bash
python scripts/probe_merchants.py
```

`data/merchants.csv` is committed so deploys have a working registry.

## Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

Expose with a tunnel (or deploy to Northflank) and point Linq’s webhook at `{PUBLIC_BASE_URL}/webhook/linq`. Keep `PUBLIC_BASE_URL` in `.env` in sync with the public HTTPS base (no trailing slash).

Health check: `GET /health`

## Deploy (Northflank)

Combined service builds from the root `Dockerfile`, exposes port `8000`, and probes `GET /health`. Set runtime secrets from `.env.example`, then set `PUBLIC_BASE_URL` to the service’s public `*.code.run` URL and point the Linq webhook at `{PUBLIC_BASE_URL}/webhook/linq`.

## Environment

See `.env.example`. Required:

| Variable | Purpose |
|---|---|
| `LINQ_API_TOKEN` / `LINQ_WEBHOOK_SECRET` | iMessage send + webhook auth |
| `OPENAI_API_KEY` | Vision + Luna matching |
| `PRAVA_*` | Sandbox sessions + callbacks |
| `PUBLIC_BASE_URL` | Tunnel URL for Linq + Prava callbacks |
| `SUPABASE_DB_URL` | Postgres |
| `DEMO_USER_PHONE` | Fallback Prava customer id seed phone (users auto-created on first message) |

## Tests

```bash
pytest -q
```

## Layout

```
app/           FastAPI app, orchestrator, vision, resolver, Prava, shelf
migrations/    SQL schema
scripts/       Merchant probe helper
tests/         pytest suite
```
