from pathlib import Path

from app.vision import identify

FIXTURE = Path(__file__).parent / "fixtures" / "minimalist_serum.jpg"


async def test_identify_real_product_photo():
    image_bytes = FIXTURE.read_bytes()
    result = await identify(image_bytes)
    assert result.brand is not None
    assert 0.0 <= result.confidence <= 1.0
    assert result.reasoning


def test_system_prompt_includes_exact_registry_categories():
    from app.categories import load_known_categories
    from app.vision import _build_system_prompt

    prompt = _build_system_prompt(load_known_categories())

    assert "Beauty & Personal Care/Skin Care" in prompt
    assert "Health/Pharmacy" in prompt
    assert "otherwise set category to null" in prompt
