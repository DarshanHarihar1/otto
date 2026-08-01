from pathlib import Path

from app.registry import load_registry

_FALLBACK_KNOWN_CATEGORIES = frozenset(
    {
        "Beauty & Personal Care/Skin Care",
        "Health/Pharmacy",
        "Health/Health Conditions & Concerns",
    }
)


def _registry_path() -> str:
    data_dir = Path(__file__).resolve().parent.parent / "data"
    merchants_path = data_dir / "merchants.csv"
    if not merchants_path.exists():
        merchants_path = data_dir / "merchants_source.csv"
    return str(merchants_path)


def load_known_categories() -> frozenset[str]:
    """Load categories that the current merchant registry can fulfill."""
    try:
        categories = load_registry(_registry_path()).categories()
        return frozenset(categories) or _FALLBACK_KNOWN_CATEGORIES
    except FileNotFoundError:
        return _FALLBACK_KNOWN_CATEGORIES
