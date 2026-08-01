import asyncio
import csv
import sys

import httpx


async def probe(client: httpx.AsyncClient, domain: str) -> dict:
    for host in {domain, f"{domain.split('.')[0]}.myshopify.com"}:
        try:
            r = await client.get(
                f"https://{host}/search/suggest.json",
                params={"q": "serum", "resources[type]": "product"},
                timeout=8,
                follow_redirects=True,
            )
            if r.status_code == 200 and "resources" in r.text:
                return {"domain": host, "category": "", "ok": True, "status": 200}
        except Exception as e:
            last = type(e).__name__
        else:
            last = r.status_code
    return {"domain": domain, "category": "", "ok": False, "status": last}


async def main(rows: list[dict]) -> list[dict]:
    async with httpx.AsyncClient(headers={"User-Agent": "otto/1.0"}) as client:
        results = await asyncio.gather(*[probe(client, r["Domain"]) for r in rows])
    for result, row in zip(results, rows):
        result["category"] = row.get("Category", "")
    verified = [r for r in results if r["ok"]]
    print(f"{len(verified)}/{len(results)} merchants respond", file=sys.stderr)
    return verified


if __name__ == "__main__":
    with open("data/merchants_source.csv") as f:
        rows = list(csv.DictReader(f))
    verified = asyncio.run(main(rows))
    with open("data/merchants.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["domain", "category", "ok"])
        writer.writeheader()
        for r in verified:
            writer.writerow({"domain": r["domain"], "category": r["category"], "ok": r["ok"]})
