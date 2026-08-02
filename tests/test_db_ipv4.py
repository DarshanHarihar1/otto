from urllib.parse import parse_qs, urlparse

from app.db import _prefer_ipv4_dsn


def test_prefer_ipv4_dsn_sets_hostaddr(monkeypatch):
    monkeypatch.setattr(
        "app.db.socket.getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("1.2.3.4", 5432))],
    )
    dsn = _prefer_ipv4_dsn(
        "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"
    )
    parsed = urlparse(dsn)
    assert parsed.hostname == "db.example.supabase.co"
    assert parse_qs(parsed.query)["hostaddr"] == ["1.2.3.4"]


def test_prefer_ipv4_dsn_passthrough_when_no_a_record(monkeypatch):
    def _fail(*args, **kwargs):
        raise OSError("no A record")

    monkeypatch.setattr("app.db.socket.getaddrinfo", _fail)
    original = "postgresql://postgres:secret@db.example.supabase.co:5432/postgres"
    assert _prefer_ipv4_dsn(original) == original
