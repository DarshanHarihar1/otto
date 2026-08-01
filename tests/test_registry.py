import csv
from pathlib import Path

import pytest

from app.registry import load_registry


def test_load_registry_from_real_merchants_csv():
    registry = load_registry("data/merchants.csv")
    assert len(registry.categories()) > 0
    assert len(registry.all_domains()) > 0


def test_load_registry_filters_ok_truthy_case_insensitive(tmp_path: Path):
    csv_path = tmp_path / "merchants.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["domain", "category", "ok"])
        writer.writeheader()
        writer.writerow(
            {"domain": "a.com", "category": "Cat/A", "ok": "True"}
        )
        writer.writerow(
            {"domain": "b.com", "category": "Cat/A", "ok": "true"}
        )
        writer.writerow(
            {"domain": "c.com", "category": "Cat/B", "ok": "FALSE"}
        )
        writer.writerow(
            {"domain": "d.com", "category": "Cat/B", "ok": ""}
        )

    registry = load_registry(str(csv_path))

    assert registry.categories() == {"Cat/A"}
    assert registry.domains_for_category("Cat/A") == ["a.com", "b.com"]
    assert registry.domains_for_category("Cat/B") == []
    assert registry.all_domains() == ["a.com", "b.com"]
