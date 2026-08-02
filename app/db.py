import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import psycopg

from app.config import settings


def _prefer_ipv4_dsn(dsn: str) -> str:
    """Force libpq to dial an A-record address.

    Some hosts (e.g. Northflank) cannot open IPv6 sockets; Supabase direct
    hostnames often resolve to AAAA first and fail with
    \"Cannot assign requested address\". Keep `host` for TLS SNI/verify and set
    `hostaddr` to an IPv4 literal when one exists.
    """
    parsed = urlparse(dsn)
    host = parsed.hostname
    if not host:
        return dsn
    port = parsed.port or 5432
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return dsn
    if not infos:
        return dsn
    ipv4 = infos[0][4][0]
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if query.get("hostaddr") == ipv4:
        return dsn
    query["hostaddr"] = ipv4
    return urlunparse(parsed._replace(query=urlencode(query)))


def get_conn() -> psycopg.Connection:
    return psycopg.connect(_prefer_ipv4_dsn(settings.supabase_db_url), autocommit=True)
