import httpx


async def search_suggest(domain: str, query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        try:
            r = await client.get(
                f"https://{domain}/search/suggest.json",
                params={"q": query, "resources[type]": "product", "resources[limit]": 10},
            )
            r.raise_for_status()
            return r.json().get("resources", {}).get("results", {}).get("products", [])
        except Exception:
            return []


async def get_product(domain: str, handle: str) -> dict:
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        r = await client.get(f"https://{domain}/products/{handle}.json")
        r.raise_for_status()
        return r.json()["product"]
