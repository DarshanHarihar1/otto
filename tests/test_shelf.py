from app.config import settings
from app.db import get_conn
from app.shelf import find_shelf_item


def _backfill_label_if_missing():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE items i SET label = i.brand || ' ' || i.product, updated_at = now()
               FROM users u
               WHERE i.user_id = u.id AND u.phone = %s AND i.state = 'ORDERED' AND i.label IS NULL""",
            (settings.demo_user_phone,),
        )


def test_find_shelf_item_matches_real_ordered_item():
    _backfill_label_if_missing()
    result = find_shelf_item(settings.demo_user_phone, "trimmer")
    assert result is not None
    assert result.item_id
    assert result.last_price_paise > 0
