import csv
from dataclasses import dataclass, field


@dataclass
class Registry:
    _by_category: dict[str, list[str]] = field(default_factory=dict)

    def categories(self) -> set[str]:
        return set(self._by_category.keys())

    def domains_for_category(self, category: str) -> list[str]:
        return self._by_category.get(category, [])

    def all_domains(self) -> list[str]:
        return [d for domains in self._by_category.values() for d in domains]


def load_registry(path: str = "data/merchants.csv") -> Registry:
    by_category: dict[str, list[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("ok", "").strip().lower() != "true":
                continue
            by_category.setdefault(row["category"], []).append(row["domain"])
    return Registry(_by_category=by_category)
