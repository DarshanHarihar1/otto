import asyncio
import base64

from openai import OpenAI
from pydantic import BaseModel

from app.config import settings

_client = OpenAI(api_key=settings.openai_api_key)


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


def _parse_identification(b64: str) -> Identification | None:
    response = _client.responses.parse(
        model="gpt-5.6-sol",
        input=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Identify this product."},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}",
                    },
                ],
            },
        ],
        text_format=Identification,
    )
    return response.output_parsed


async def identify(image_bytes: bytes) -> Identification | None:
    b64 = base64.b64encode(image_bytes).decode()
    return await asyncio.to_thread(_parse_identification, b64)
