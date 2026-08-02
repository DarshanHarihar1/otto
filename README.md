# Otto

Photograph a product in iMessage. Otto identifies it, finds a live Shopify price, and lets you buy it in one tap via Prava.

## What it does

1. **Photo in** — Linq webhook delivers an iMessage image
2. **Identify** — OpenAI vision names brand, product, size, and category
3. **Resolve** — Search Shopify merchants (brand store first, then category fan-out)
4. **Quote** — Text back price; optionally offer a cheaper same-brand alt elsewhere
5. **Checkout** — Tapback or “yes” opens a Prava approval link (sandbox passkey)
6. **Shelf** — Paid items can refill later on mandate

Also supports brand substitution when the exact SKU isn’t found (blocked in health categories), and mandate refills with a spend cap.

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

Optional: probe merchants into `data/merchants.csv` (gitignored):

```bash
python scripts/probe_merchants.py
```

## Run locally

```bash
uvicorn app.main:app --reload --port 8000
```

Expose with a tunnel and point Linq’s webhook at `{PUBLIC_BASE_URL}/webhook/linq`. Keep `PUBLIC_BASE_URL` in `.env` in sync with the tunnel URL.

Health check: `GET /health`

## Environment

See `.env.example`. Required:

| Variable | Purpose |
|---|---|
| `LINQ_API_TOKEN` / `LINQ_WEBHOOK_SECRET` | iMessage send + webhook auth |
| `OPENAI_API_KEY` | Vision + Luna matching |
| `PRAVA_*` | Sandbox sessions + callbacks |
| `PUBLIC_BASE_URL` | Tunnel URL for Linq + Prava callbacks |
| `SUPABASE_DB_URL` | Postgres |
| `DEMO_USER_PHONE` | Phone allowed for demos |

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
