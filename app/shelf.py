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
