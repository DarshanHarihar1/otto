import httpx
from supabase import create_client

from app.config import settings

_storage = create_client(settings.supabase_url, settings.supabase_service_key)


async def download_media(url: str) -> bytes:
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


def archive_photo(item_id: str, image_bytes: bytes) -> str:
    path = f"{item_id}.jpg"
    _storage.storage.from_("photos").upload(
        path, image_bytes, {"content-type": "image/jpeg", "upsert": "true"}
    )
    return path
