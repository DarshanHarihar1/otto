import asyncio

from openai import OpenAI
from pydantic import BaseModel

from app.config import settings
from app.vision import Identification

_client = OpenAI(api_key=settings.openai_api_key)

_MODEL = "gpt-5.6-luna"


class VariantMatch(BaseModel):
    best_match_handle: str | None
    similarity: float
    shared_attributes: list[str]
    differences: list[str]
    one_line_pitch: str


class ShopifyVariantMatch(BaseModel):
    shopify_variant_id: str | None


def _match_variant_sync(
    identification: Identification, candidates: list[dict]
) -> VariantMatch:
    candidate_summary = "\n".join(
        f"- handle={c.get('handle')} title={c.get('title')}" for c in candidates
    )
    response = _client.responses.parse(
        model=_MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "Given a target product identification and a list of candidate "
                    "Shopify products, pick the single best match. `similarity` is "
                    "0-1; below ~0.6 the match isn't worth offering. List concrete "
                    "shared_attributes and differences, not vague ones."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target: brand={identification.brand} "
                    f"product={identification.product} variant={identification.variant}\n"
                    f"Candidates:\n{candidate_summary}"
                ),
            },
        ],
        text_format=VariantMatch,
    )
    return response.output_parsed


async def match_variant(
    identification: Identification, candidates: list[dict]
) -> VariantMatch:
    return await asyncio.to_thread(_match_variant_sync, identification, candidates)


def _match_shopify_variant_sync(
    identification: Identification, variants: list[dict]
) -> ShopifyVariantMatch:
    variant_summary = "\n".join(
        f"- id={v.get('id')} title={v.get('title')} option1={v.get('option1')} "
        f"option2={v.get('option2')} option3={v.get('option3')}"
        for v in variants
    )
    response = _client.responses.parse(
        model=_MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "Given a target product identification and Shopify variants, "
                    "select the variant that matches the requested size, shade, "
                    "concentration, count, or other variant detail. Return only "
                    "the id of a supplied variant. Return null when none matches."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Target: brand={identification.brand} "
                    f"product={identification.product} variant={identification.variant}\n"
                    f"Shopify variants:\n{variant_summary}"
                ),
            },
        ],
        text_format=ShopifyVariantMatch,
    )
    return response.output_parsed


async def match_shopify_variant(
    identification: Identification, variants: list[dict]
) -> ShopifyVariantMatch:
    return await asyncio.to_thread(_match_shopify_variant_sync, identification, variants)
