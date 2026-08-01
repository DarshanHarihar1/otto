from app.db import get_conn


def test_can_query_users_table():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM users")
            row = cur.fetchone()
    assert row[0] >= 1
