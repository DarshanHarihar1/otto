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
