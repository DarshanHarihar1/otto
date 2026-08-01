import asyncio
import base64
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Collection
from pathlib import Path

from openai import OpenAI, RateLimitError
from pydantic import BaseModel

from app.categories import load_known_categories
from app.config import settings

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=settings.openai_api_key)

_PRIMARY_MODEL = "gpt-5.6-sol"
_FALLBACK_MODEL = "gpt-5.6-terra"
_MAX_IMAGE_EDGE = 1280


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
    """Shrink image bytes when a request is too large for TPM limits.

    Prefers Pillow when installed; falls back to macOS `sips`; otherwise
    returns the original bytes (caller still switches to detail=low).
    """
    try:
        from PIL import Image  # type: ignore
        import io

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img.thumbnail((_MAX_IMAGE_EDGE, _MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=80, optimize=True)
            return out.getvalue()
    except Exception:
        logger.info("Pillow downscale unavailable; trying sips if present")

    if shutil.which("sips"):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.jpg"
            dst = Path(tmp) / "out.jpg"
            src.write_bytes(image_bytes)
            subprocess.run(
                ["sips", "-Z", str(_MAX_IMAGE_EDGE), str(src), "--out", str(dst)],
                check=True,
                capture_output=True,
            )
            if dst.exists() and dst.stat().st_size > 0:
                return dst.read_bytes()

    logger.warning("Could not downscale image; retrying original bytes with detail=low")
    return image_bytes


def _parse_identification(
    b64: str,
    known_categories: Collection[str],
    *,
    model: str,
    detail: str = "auto",
) -> Identification | None:
    response = _client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": _build_system_prompt(known_categories)},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Identify this product."},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}",
                        "detail": detail,
                    },
                ],
            },
        ],
        text_format=Identification,
    )
    return response.output_parsed


def _identify_sync(
    image_bytes: bytes, known_categories: Collection[str]
) -> Identification | None:
    """Try sol → on 429 terra (same image) → on 429 downscale + terra detail=low."""
    payload = image_bytes
    b64 = base64.b64encode(payload).decode()
    model, detail = _PRIMARY_MODEL, "auto"
    tried_fallback = False
    tried_downscale = False

    while True:
        try:
            logger.info("Vision identify attempt model=%s detail=%s", model, detail)
            return _parse_identification(
                b64, known_categories, model=model, detail=detail
            )
        except RateLimitError as exc:
            logger.warning("Vision rate-limited on model=%s: %s", model, exc)
            if not tried_fallback:
                tried_fallback = True
                model, detail = _FALLBACK_MODEL, "auto"
                continue
            if not tried_downscale:
                tried_downscale = True
                payload = _downscale_for_vision(image_bytes)
                b64 = base64.b64encode(payload).decode()
                model, detail = _FALLBACK_MODEL, "low"
                logger.info(
                    "Retrying vision after downscale (%s → %s bytes)",
                    len(image_bytes),
                    len(payload),
                )
                continue
            raise


async def identify(
    image_bytes: bytes, known_categories: Collection[str] | None = None
) -> Identification | None:
    cats = known_categories or load_known_categories()
    return await asyncio.to_thread(_identify_sync, image_bytes, cats)
