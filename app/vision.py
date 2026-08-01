import asyncio
import base64
import io
from collections.abc import Collection

from openai import OpenAI
from PIL import Image
from pydantic import BaseModel

from app.categories import load_known_categories
from app.config import settings

_client = OpenAI(api_key=settings.openai_api_key)

# Phone photos easily blow past the org's 10k TPM vision budget; downscale first.
_MAX_IMAGE_EDGE = 1280
_JPEG_QUALITY = 80


class Identification(BaseModel):
    object_type: str
    brand: str | None
    product: str | None
    variant: str | None
    category: str | None
    search_terms: list[str]
    confidence: float
    reasoning: str
    missing_info: str | None
    suggested_photo: str | None


_SYSTEM_PROMPT = (
    "You identify a physical retail product from a photo, typically an empty "
    "or near-empty container the user wants to repurchase. Return brand, exact "
    "product name, and variant (size/shade/concentration/count) as precisely as "
    "the label allows. If you cannot read a detail confidently, leave it null, "
    "explain what's missing in `missing_info`, and name the exact photo angle "
    "that would resolve it in `suggested_photo`. `confidence` reflects how sure "
    "you are of brand+product+variant together, not just object_type."
)


def _build_system_prompt(known_categories: Collection[str]) -> str:
    # Compact list — bullet lines burn tokens on every call.
    registry_categories = "; ".join(sorted(known_categories))
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        "For `category`, choose exactly one string from this merchant registry "
        "when the product maps to one of them. Do not invent, reword, or "
        "broaden categories. If no registry category fits or the category "
        "cannot be determined confidently, set category to null. "
        f"Registry: {registry_categories}"
    )


def _downscale_for_vision(image_bytes: bytes) -> bytes:
    """Shrink/re-encode so Responses API image tokens stay under TPM limits."""
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        img.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
        return out.getvalue()


def _parse_identification(
    b64: str, known_categories: Collection[str]
) -> Identification | None:
    response = _client.responses.parse(
        model="gpt-5.6-sol",
        input=[
            {"role": "system", "content": _build_system_prompt(known_categories)},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Identify this product."},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}",
                        "detail": "low",
                    },
                ],
            },
        ],
        text_format=Identification,
    )
    return response.output_parsed


async def identify(
    image_bytes: bytes, known_categories: Collection[str] | None = None
) -> Identification | None:
    compact = await asyncio.to_thread(_downscale_for_vision, image_bytes)
    b64 = base64.b64encode(compact).decode()
    return await asyncio.to_thread(
        _parse_identification, b64, known_categories or load_known_categories()
    )
