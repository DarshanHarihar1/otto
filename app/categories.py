import csv
from pathlib import Path


_FALLBACK_KNOWN_CATEGORIES = frozenset(
    {
        "Beauty & Personal Care/Skin Care",
        "Health/Pharmacy",
        "Health/Health Conditions & Concerns",
    }
)


def load_known_categories() -> frozenset[str]:
    """Load categories that the current merchant registry can fulfill."""
    data_dir = Path(__file__).resolve().parent.parent / "data"
    merchants_path = data_dir / "merchants.csv"
    if not merchants_path.exists():
        merchants_path = data_dir / "merchants_source.csv"

    try:
        with merchants_path.open(newline="", encoding="utf-8") as merchants_file:
            rows = csv.DictReader(merchants_file)
            category_key = "category" if "category" in (rows.fieldnames or []) else "Category"
            has_ok_column = "ok" in (rows.fieldnames or [])
            categories = frozenset(
                row[category_key]
                for row in rows
                if row.get(category_key)
                and (
                    not has_ok_column
                    or row.get("ok", "").strip().lower() == "true"
                )
            )
    except FileNotFoundError:
        return _FALLBACK_KNOWN_CATEGORIES
    return categories or _FALLBACK_KNOWN_CATEGORIES
