import psycopg
from app.config import settings


def get_conn() -> psycopg.Connection:
    conn = psycopg.connect(settings.supabase_db_url, autocommit=True)
    return conn
